from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Text, Update, cast, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from acme.om.exceptions import NotFound, PreconditionFailed, TenantMismatch
from acme.om.outbox.storage.tables.outbox_rows import OutboxRows
from acme.om.outbox.types.row import OutboxRow
from acme.om.spend_budgets.rules import SpendTallyKey, key_of, refusal_of
from acme.om.spend_budgets.storage import LedgerStorageInterface, SpendBudgetStorageInterface
from acme.om.spend_budgets.storage.tables.spend_budget_holds import SpendBudgetHolds
from acme.om.spend_budgets.storage.tables.spend_budget_settlements import SpendBudgetSettlements
from acme.om.spend_budgets.storage.tables.spend_budget_tallies import SpendBudgetTallies
from acme.om.spend_budgets.storage.tables.spend_budgets import SpendBudgets
from acme.om.spend_budgets.storage.tables.usage_records import UsageRecords
from acme.om.spend_budgets.types.amount import Spend
from acme.om.spend_budgets.types.spend_breach import Refusal
from acme.om.spend_budgets.types.spend_budget import SpendBudget, SpendBudgetScope
from acme.om.spend_budgets.types.spend_hold import (
    SpendHold,
    SpendHoldLine,
    SpendSettlement,
    SpendTally,
)
from acme.om.spend_budgets.types.usage import LoopUsage, UsageRecord, UsageRollup
from acme.om.storage.impl.pg_base import PgStorageBase, delete_batch, deleted
from acme.om.storage.utils.translation import to_model, to_row, to_values


def cas_statement(org_id: UUID, budget: SpendBudget, expected_version: int) -> Update:
    """The compare-and-set of one budget: the version is in the WHERE, so two
    writers from one snapshot cannot both land. Returns the id when it hit."""
    values = {k: v for k, v in to_values(budget, SpendBudgets).items() if k != "id"}
    return (
        update(SpendBudgets)
        .where(
            SpendBudgets.id == budget.id,
            SpendBudgets.org_id == org_id,
            SpendBudgets.version == expected_version,
        )
        .values(**values)
        .returning(SpendBudgets.id)
    )


class SpendBudgetStoragePostgresImpl(PgStorageBase, SpendBudgetStorageInterface):
    async def create_spend_budget(
        self, org_id: UUID, budget: SpendBudget, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        return await self._insert(SpendBudgets, org_id, budget, outbox_rows)

    async def read_spend_budget(self, org_id: UUID, spend_budget_id: UUID) -> SpendBudget | None:
        stmt = select(SpendBudgets).where(
            SpendBudgets.org_id == org_id, SpendBudgets.id == spend_budget_id
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, SpendBudget)

    async def read_spend_budgets(
        self, org_id: UUID, after: UUID | None, limit: int
    ) -> list[SpendBudget]:
        stmt = select(SpendBudgets).where(SpendBudgets.org_id == org_id)
        if after is not None:
            stmt = stmt.where(SpendBudgets.id > after)
        stmt = stmt.order_by(SpendBudgets.id).limit(limit)
        async with self._session_for(stmt, org_id=org_id) as session:
            return [to_model(row, SpendBudget) for row in (await session.execute(stmt)).scalars()]

    async def read_spend_budgets_for(
        self, org_id: UUID, scopes: Sequence[SpendBudgetScope], limit: int
    ) -> list[SpendBudget]:
        pairs = sorted({(scope.kind.value, scope.key) for scope in scopes})
        if not pairs:
            return []
        stmt = (
            select(SpendBudgets)
            .where(
                SpendBudgets.org_id == org_id,
                tuple_(SpendBudgets.scope_kind, SpendBudgets.scope_key).in_(pairs),
                # A literal test, so a generic plan proves the partial index.
                SpendBudgets.closed_at.is_(None),
            )
            .order_by(SpendBudgets.id)
            .limit(limit)
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            return [to_model(row, SpendBudget) for row in (await session.execute(stmt)).scalars()]

    async def write_spend_budget(
        self,
        org_id: UUID,
        budget: SpendBudget,
        expected_version: int,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> None:
        async with self._session_for(SpendBudgets, org_id=org_id) as db:
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
        stmt = delete_batch(SpendBudgets, SpendBudgets.org_id == org_id, limit=limit)
        async with self._session_for(stmt, org_id=org_id) as session:
            purged = deleted(await session.execute(stmt))
            await session.commit()
            return purged


class LedgerStoragePostgresImpl(PgStorageBase, LedgerStorageInterface):
    async def open_spend_hold(self, org_id: UUID, hold: SpendHold) -> Refusal | None:
        async with self._session_for(SpendBudgetHolds, org_id=org_id) as db:
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
                db.add(to_row(hold, SpendBudgetHolds, org_id=org_id))
                await db.commit()
            except IntegrityError as error:
                # The same hold opened at once with no line to queue on, or an
                # id another tenant holds: the rollback takes the tallies back.
                await db.rollback()
                if await self.read_hold(org_id, hold.id) is None:
                    raise TenantMismatch(f"hold {hold.id} is not in {org_id}") from error
                return None
            return None

    async def read_hold(self, org_id: UUID, spend_hold_id: UUID) -> SpendHold | None:
        stmt = select(SpendBudgetHolds).where(
            SpendBudgetHolds.org_id == org_id, SpendBudgetHolds.id == spend_hold_id
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, SpendHold)

    async def close_spend_hold(self, org_id: UUID, settlement: SpendSettlement) -> SpendSettlement:
        async with self._session_for(SpendBudgetSettlements, org_id=org_id) as db:
            try:
                row = await _hold_row(db, org_id, settlement.spend_hold_id)
                if row is None:
                    await db.rollback()
                    raise NotFound(f"hold {settlement.spend_hold_id} not found")
                hold = to_model(row, SpendHold)
                await _lock_tallies(db, org_id, hold.lines)
                # Read after the lock: a settlement that got there first has
                # committed by now, and it is the one that counts.
                first = await _settlement_row(db, org_id, hold.id)
                if first is not None:
                    # Read before the rollback, which expires the row.
                    stored = to_model(first, SpendSettlement)
                    await db.rollback()
                    return stored
                for line in hold.lines:
                    await db.execute(
                        _moved(org_id, line, held=hold.exposure, spent=settlement.spent)
                    )
                db.add(to_row(settlement, SpendBudgetSettlements, org_id=org_id))
                await db.commit()
            except IntegrityError:
                # A settlement of the same hold landed at once, with no line to
                # queue on: the rollback takes the tallies back.
                await db.rollback()
                found = await self.read_spend_settlement(org_id, settlement.spend_hold_id)
                if found is None:
                    raise
                return found
            return settlement

    async def read_spend_settlement(
        self, org_id: UUID, spend_hold_id: UUID
    ) -> SpendSettlement | None:
        stmt = select(SpendBudgetSettlements).where(
            SpendBudgetSettlements.org_id == org_id,
            SpendBudgetSettlements.spend_hold_id == spend_hold_id,
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, SpendSettlement)

    async def read_spend_tally(
        self, org_id: UUID, spend_budget_id: UUID, window_start: datetime
    ) -> SpendTally | None:
        stmt = select(SpendBudgetTallies).where(
            SpendBudgetTallies.org_id == org_id,
            SpendBudgetTallies.spend_budget_id == spend_budget_id,
            SpendBudgetTallies.window_start == window_start,
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, SpendTally)

    async def append_usage_record(self, org_id: UUID, record: UsageRecord) -> bool:
        """The insert meets the hold's unique key when the tenant holds a
        record of it, and lands nothing; another tenant's id is the primary
        key's, and refused."""
        insert = (
            pg_insert(UsageRecords)
            .values(org_id=org_id, **to_values(record, UsageRecords))
            .on_conflict_do_nothing(
                index_elements=[UsageRecords.org_id, UsageRecords.spend_hold_id]
            )
            .returning(UsageRecords.id)
        )
        async with self._session_for(UsageRecords, org_id=org_id) as session:
            try:
                landed = (await session.execute(insert)).scalar_one_or_none() is not None
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                raise TenantMismatch(f"usage record {record.id} is not in {org_id}") from error
            return landed

    async def read_usage_records(
        self, org_id: UUID, agent_session_id: UUID, after: UUID | None, limit: int
    ) -> list[UsageRecord]:
        stmt = select(UsageRecords).where(
            UsageRecords.org_id == org_id, UsageRecords.agent_session_id == agent_session_id
        )
        if after is not None:
            stmt = stmt.where(UsageRecords.id > after)
        stmt = stmt.order_by(UsageRecords.id).limit(limit)
        async with self._session_for(stmt, org_id=org_id) as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [to_model(row, UsageRecord) for row in rows]

    async def read_usage_rollups(
        self, org_id: UUID, agent_session_id: UUID, limit: int
    ) -> list[LoopUsage]:
        u = UsageRecords
        stmt = (
            select(u.loop_id, *_ROLLUP)
            .where(u.org_id == org_id, u.agent_session_id == agent_session_id)
            .group_by(u.loop_id)
            # Postgres has no min of a uuid; its text sorts as its bytes do.
            .order_by(func.min(cast(u.id, Text)))
            .limit(limit)
        )
        async with self._session_for(UsageRecords, org_id=org_id) as session:
            rows = (await session.execute(stmt)).all()
        return [LoopUsage(loop_id=row[0], rollup=_rollup(row[1:])) for row in rows]

    async def read_usage_total(self, org_id: UUID, agent_session_id: UUID) -> UsageRollup:
        u = UsageRecords
        stmt = select(*_ROLLUP).where(u.org_id == org_id, u.agent_session_id == agent_session_id)
        async with self._session_for(UsageRecords, org_id=org_id) as session:
            row = (await session.execute(stmt)).one()
        return _rollup(row)

    async def count_tenant(self, org_id: UUID, limit: int) -> int:
        # Each count stops at the limit.
        counts = [
            select(func.count())
            .select_from(select(column).where(table.org_id == org_id).limit(limit).subquery())
            .scalar_subquery()
            for table, column in (
                (SpendBudgetHolds, SpendBudgetHolds.id),
                (SpendBudgetSettlements, SpendBudgetSettlements.id),
                (SpendBudgetTallies, SpendBudgetTallies.spend_budget_id),
            )
        ]
        stmt = select(counts[0] + counts[1] + counts[2])
        async with self._session_for(SpendBudgetHolds, org_id=org_id) as session:
            return min(int((await session.execute(stmt)).scalar_one()), limit)


_ROLLUP = (
    func.count(),
    func.coalesce(func.sum(UsageRecords.input_tokens), 0),
    func.coalesce(func.sum(UsageRecords.cache_read_tokens), 0),
    func.coalesce(func.sum(UsageRecords.cache_write_tokens), 0),
    func.coalesce(func.sum(UsageRecords.output_tokens), 0),
    func.coalesce(func.sum(UsageRecords.thinking_tokens), 0),
    func.coalesce(func.sum(UsageRecords.cost_micros), 0),
    func.count().filter(UsageRecords.cost_micros.is_(None)),
    func.count().filter(UsageRecords.settled_whole),
    func.coalesce(func.sum(UsageRecords.latency_ms), 0),
)
"""The columns of a rollup, in `UsageRollup`'s order: a cost no price gave
is in no sum, and counts as unpriced; a call settled whole counts apart."""


def _rollup(row: Sequence[Any]) -> UsageRollup:
    (
        calls,
        input_tokens,
        cache_read,
        cache_write,
        output,
        thinking,
        cost,
        unpriced,
        whole,
        latency,
    ) = row
    return UsageRollup(
        calls=calls,
        input_tokens=input_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        output_tokens=output,
        thinking_tokens=thinking,
        cost_micros=cost,
        unpriced=unpriced,
        settled_whole=whole,
        latency_ms=latency,
    )


async def _lock_tallies(
    db: AsyncSession, org_id: UUID, lines: Sequence[SpendHoldLine]
) -> dict[SpendTallyKey, SpendTally]:
    """The tally of each line, made where its window has none, then locked
    to the commit. Both statements take the rows in one order, by budget then
    window, so two calls over the same lines queue and never deadlock."""
    keys = sorted({key_of(line) for line in lines})
    if not keys:
        return {}
    await db.execute(
        pg_insert(SpendBudgetTallies)
        .values(
            [
                {
                    "org_id": org_id,
                    "spend_budget_id": spend_budget_id,
                    "window_start": window_start,
                    "held_cost_micros": 0,
                    "held_tokens": 0,
                    "spent_cost_micros": 0,
                    "spent_tokens": 0,
                }
                for spend_budget_id, window_start in keys
            ]
        )
        .on_conflict_do_nothing()
    )
    locked = (
        select(SpendBudgetTallies)
        .where(
            SpendBudgetTallies.org_id == org_id,
            tuple_(SpendBudgetTallies.spend_budget_id, SpendBudgetTallies.window_start).in_(keys),
        )
        .order_by(SpendBudgetTallies.spend_budget_id, SpendBudgetTallies.window_start)
        .with_for_update()
    )
    rows = (await db.execute(locked)).scalars().all()
    return {(row.spend_budget_id, row.window_start): to_model(row, SpendTally) for row in rows}


def _moved(org_id: UUID, line: SpendHoldLine, *, held: Spend, spent: Spend | None = None) -> Update:
    """A line's tally as a hold moves it: an open adds its worst case to what
    the window holds; a settlement (`spent`) takes it back and adds what it
    spent. A cost no price gave counts as nothing."""
    tally = SpendBudgetTallies
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
            tally.spend_budget_id == line.spend_budget_id,
            tally.window_start == line.window_start,
        )
        .values(**values)
    )


async def _hold_row(db: AsyncSession, org_id: UUID, spend_hold_id: UUID) -> SpendBudgetHolds | None:
    stmt = select(SpendBudgetHolds).where(
        SpendBudgetHolds.org_id == org_id, SpendBudgetHolds.id == spend_hold_id
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _settlement_row(
    db: AsyncSession, org_id: UUID, spend_hold_id: UUID
) -> SpendBudgetSettlements | None:
    stmt = select(SpendBudgetSettlements).where(
        SpendBudgetSettlements.org_id == org_id,
        SpendBudgetSettlements.spend_hold_id == spend_hold_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()
