from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import Update, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from acme.om.budgets.rules import TallyKey, key_of, refusal_of
from acme.om.budgets.storage import BudgetStorageInterface, LedgerStorageInterface
from acme.om.budgets.storage.tables.budget_holds import BudgetHolds
from acme.om.budgets.storage.tables.budget_settlements import BudgetSettlements
from acme.om.budgets.storage.tables.budget_tallies import BudgetTallies
from acme.om.budgets.storage.tables.budgets import Budgets
from acme.om.budgets.types.amount import Spend
from acme.om.budgets.types.breach import Refusal
from acme.om.budgets.types.budget import Budget, BudgetScope
from acme.om.budgets.types.hold import Hold, HoldLine, Settlement, Tally
from acme.om.exceptions import NotFound, PreconditionFailed, TenantMismatch
from acme.om.outbox.storage.tables.outbox_rows import OutboxRows
from acme.om.outbox.types.row import OutboxRow
from acme.om.storage.impl.pg_base import PgStorageBase, delete_batch, deleted
from acme.om.storage.utils.translation import to_model, to_row, to_values


def cas_statement(org_id: UUID, budget: Budget, expected_version: int) -> Update:
    """The compare-and-set of one budget: the version is in the WHERE, so two
    writers from one snapshot cannot both land. Returns the id when it hit."""
    values = {k: v for k, v in to_values(budget, Budgets).items() if k != "id"}
    return (
        update(Budgets)
        .where(
            Budgets.id == budget.id,
            Budgets.org_id == org_id,
            Budgets.version == expected_version,
        )
        .values(**values)
        .returning(Budgets.id)
    )


class BudgetStoragePostgresImpl(PgStorageBase, BudgetStorageInterface):
    async def create_budget(
        self, org_id: UUID, budget: Budget, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        return await self._insert(Budgets, org_id, budget, outbox_rows)

    async def read_budget(self, org_id: UUID, budget_id: UUID) -> Budget | None:
        stmt = select(Budgets).where(Budgets.org_id == org_id, Budgets.id == budget_id)
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, Budget)

    async def read_budgets(self, org_id: UUID, after: UUID | None, limit: int) -> list[Budget]:
        stmt = select(Budgets).where(Budgets.org_id == org_id)
        if after is not None:
            stmt = stmt.where(Budgets.id > after)
        stmt = stmt.order_by(Budgets.id).limit(limit)
        async with self._session_for(stmt, org_id=org_id) as session:
            return [to_model(row, Budget) for row in (await session.execute(stmt)).scalars()]

    async def read_budgets_for(
        self, org_id: UUID, scopes: Sequence[BudgetScope], limit: int
    ) -> list[Budget]:
        pairs = sorted({(scope.kind.value, scope.key) for scope in scopes})
        if not pairs:
            return []
        stmt = (
            select(Budgets)
            .where(
                Budgets.org_id == org_id,
                tuple_(Budgets.scope_kind, Budgets.scope_key).in_(pairs),
            )
            .order_by(Budgets.id)
            .limit(limit)
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            return [to_model(row, Budget) for row in (await session.execute(stmt)).scalars()]

    async def write_budget(
        self,
        org_id: UUID,
        budget: Budget,
        expected_version: int,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> None:
        async with self._session_for(Budgets, org_id=org_id) as db:
            stmt = cas_statement(org_id, budget, expected_version)
            if (await db.execute(stmt)).scalar_one_or_none() is None:
                # Moved by another writer, or gone, or another tenant's: in
                # each the caller's snapshot is stale.
                await db.rollback()
                raise PreconditionFailed(
                    f"budget {budget.id} is no longer at version {expected_version}"
                )
            for outbox_row in outbox_rows:
                db.add(to_row(outbox_row, OutboxRows, org_id=org_id))
            await db.commit()

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        stmt = delete_batch(Budgets, Budgets.org_id == org_id, limit=limit)
        async with self._session_for(stmt, org_id=org_id) as session:
            purged = deleted(await session.execute(stmt))
            await session.commit()
            return purged


class LedgerStoragePostgresImpl(PgStorageBase, LedgerStorageInterface):
    async def open_hold(self, org_id: UUID, hold: Hold) -> Refusal | None:
        async with self._session_for(BudgetHolds, org_id=org_id) as db:
            try:
                tallies = await _lock_tallies(db, org_id, hold.lines)
                # Read after the lock: an open of the same hold that got there
                # first has committed by now, and this one answers as it did.
                if await _hold_row(db, org_id, hold.id) is not None:
                    await db.rollback()
                    return None
                refusal = refusal_of(hold, tallies)
                if refusal is not None:
                    await db.rollback()
                    return refusal
                for line in hold.lines:
                    await db.execute(_moved(org_id, line, held=hold.exposure))
                db.add(to_row(hold, BudgetHolds, org_id=org_id))
                await db.commit()
            except IntegrityError as error:
                # The same hold opened at once with no line to queue on, or an
                # id another tenant holds: the rollback takes the tallies back.
                await db.rollback()
                if await self.read_hold(org_id, hold.id) is None:
                    raise TenantMismatch(f"hold {hold.id} is not in {org_id}") from error
                return None
            return None

    async def read_hold(self, org_id: UUID, hold_id: UUID) -> Hold | None:
        stmt = select(BudgetHolds).where(BudgetHolds.org_id == org_id, BudgetHolds.id == hold_id)
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, Hold)

    async def close_hold(self, org_id: UUID, settlement: Settlement) -> Settlement:
        async with self._session_for(BudgetSettlements, org_id=org_id) as db:
            try:
                row = await _hold_row(db, org_id, settlement.hold_id)
                if row is None:
                    await db.rollback()
                    raise NotFound(f"hold {settlement.hold_id} not found")
                hold = to_model(row, Hold)
                await _lock_tallies(db, org_id, hold.lines)
                # Read after the lock: a settlement that got there first has
                # committed by now, and it is the one that counts.
                first = await _settlement_row(db, org_id, hold.id)
                if first is not None:
                    # Read before the rollback, which expires the row.
                    stored = to_model(first, Settlement)
                    await db.rollback()
                    return stored
                for line in hold.lines:
                    await db.execute(
                        _moved(org_id, line, held=hold.exposure, spent=settlement.spent)
                    )
                db.add(to_row(settlement, BudgetSettlements, org_id=org_id))
                await db.commit()
            except IntegrityError:
                # A settlement of the same hold landed at once, with no line to
                # queue on: the rollback takes the tallies back.
                await db.rollback()
                found = await self.read_settlement(org_id, settlement.hold_id)
                if found is None:
                    raise
                return found
            return settlement

    async def read_settlement(self, org_id: UUID, hold_id: UUID) -> Settlement | None:
        stmt = select(BudgetSettlements).where(
            BudgetSettlements.org_id == org_id, BudgetSettlements.hold_id == hold_id
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, Settlement)

    async def read_tally(
        self, org_id: UUID, budget_id: UUID, window_start: datetime
    ) -> Tally | None:
        stmt = select(BudgetTallies).where(
            BudgetTallies.org_id == org_id,
            BudgetTallies.budget_id == budget_id,
            BudgetTallies.window_start == window_start,
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, Tally)

    async def count_tenant(self, org_id: UUID, limit: int) -> int:
        # Each count stops at the limit.
        counts = [
            select(func.count())
            .select_from(select(column).where(table.org_id == org_id).limit(limit).subquery())
            .scalar_subquery()
            for table, column in (
                (BudgetHolds, BudgetHolds.id),
                (BudgetSettlements, BudgetSettlements.id),
                (BudgetTallies, BudgetTallies.budget_id),
            )
        ]
        stmt = select(counts[0] + counts[1] + counts[2])
        async with self._session_for(BudgetHolds, org_id=org_id) as session:
            return min(int((await session.execute(stmt)).scalar_one()), limit)


async def _lock_tallies(
    db: AsyncSession, org_id: UUID, lines: Sequence[HoldLine]
) -> dict[TallyKey, Tally]:
    """The tally of each line, made where its window has none, then locked
    to the commit. Both statements take the rows in one order, by budget then
    window, so two calls over the same lines queue and never deadlock."""
    keys = sorted({key_of(line) for line in lines})
    if not keys:
        return {}
    await db.execute(
        pg_insert(BudgetTallies)
        .values(
            [
                {
                    "org_id": org_id,
                    "budget_id": budget_id,
                    "window_start": window_start,
                    "held_cost_micros": 0,
                    "held_tokens": 0,
                    "spent_cost_micros": 0,
                    "spent_tokens": 0,
                }
                for budget_id, window_start in keys
            ]
        )
        .on_conflict_do_nothing()
    )
    locked = (
        select(BudgetTallies)
        .where(
            BudgetTallies.org_id == org_id,
            tuple_(BudgetTallies.budget_id, BudgetTallies.window_start).in_(keys),
        )
        .order_by(BudgetTallies.budget_id, BudgetTallies.window_start)
        .with_for_update()
    )
    rows = (await db.execute(locked)).scalars().all()
    return {(row.budget_id, row.window_start): to_model(row, Tally) for row in rows}


def _moved(org_id: UUID, line: HoldLine, *, held: Spend, spent: Spend | None = None) -> Update:
    """A line's tally as a hold moves it: an open adds its worst case to what
    the window holds; a settlement (`spent`) takes it back and adds what it
    spent. A cost no price gave counts as nothing."""
    tally = BudgetTallies
    sign = 1 if spent is None else -1
    values = {
        "held_cost_micros": tally.held_cost_micros + sign * (held.cost_micros or 0),
        "held_tokens": tally.held_tokens + sign * held.tokens,
    }
    if spent is not None:
        values["spent_cost_micros"] = tally.spent_cost_micros + (spent.cost_micros or 0)
        values["spent_tokens"] = tally.spent_tokens + spent.tokens
    return (
        update(tally)
        .where(
            tally.org_id == org_id,
            tally.budget_id == line.budget_id,
            tally.window_start == line.window_start,
        )
        .values(**values)
    )


async def _hold_row(db: AsyncSession, org_id: UUID, hold_id: UUID) -> BudgetHolds | None:
    stmt = select(BudgetHolds).where(BudgetHolds.org_id == org_id, BudgetHolds.id == hold_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _settlement_row(
    db: AsyncSession, org_id: UUID, hold_id: UUID
) -> BudgetSettlements | None:
    stmt = select(BudgetSettlements).where(
        BudgetSettlements.org_id == org_id, BudgetSettlements.hold_id == hold_id
    )
    return (await db.execute(stmt)).scalar_one_or_none()
