import json
from collections.abc import Sequence
from typing import Any, cast
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Table,
    Text,
    Uuid,
    bindparam,
    delete,
    exists,
    func,
    insert,
    literal,
    select,
    true,
    update,
)
from sqlalchemy import cast as cast_
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.dml import ReturningInsert

from acme.om.agent_steps.rules import batch_refusal
from acme.om.agent_steps.storage import AgentStepStorageInterface
from acme.om.agent_steps.storage.tables.agent_step_cursors import AgentStepCursors
from acme.om.agent_steps.storage.tables.agent_steps import AgentSteps
from acme.om.agent_steps.types.agent_step import AgentStep
from acme.om.agent_steps.types.page import AgentStepCursor
from acme.om.exceptions import StaleWriter, TenantMismatch, UniqueKeyTaken, ValidationFailed
from acme.om.storage.impl.pg_base import PgStorageBase, deleted, violated_constraint
from acme.om.storage.utils.translation import to_model, to_values

STORAGE_COLUMNS = ("org_id", "agent_session_id", "seq")
"""The columns storage writes itself: the tenant and the session the call
names, and the number the cursor row hands out."""

WRITTEN: tuple[str, ...] = tuple(
    name for name in cast(Table, AgentSteps.__table__).c.keys() if name not in STORAGE_COLUMNS
)
"""The columns an append writes from the step."""

JSON_COLUMNS = frozenset({"refs", "header", "content", "children"})
"""Columns that travel as JSON text in the append's arrays and are cast back
in the statement: an array of JSON documents is one dimension, where an
array of arrays would have to be rectangular."""


def _append_statement(*, fenced: bool) -> ReturningInsert[Any]:
    """The append, in one statement, as the event stream's is. The steps
    arrive as one array per column, so the statement is the same for one
    step and for a hundred. Those not yet stored take the next numbers from
    the session's cursor row, `head + n` under the row's lock, and are
    written with them, in the order given. The lock is held from this
    statement to the commit: two appends to one session queue on it, each
    leaves with the next contiguous run, and they commit in the order of
    their numbers.

    A run's append (`fenced`) moves the row only where it holds the run's
    epoch, so a row at another epoch, or no row, hands out no number and
    nothing is written. The wait on the lock reads the row again once the
    holder commits, so a run that began meanwhile fences this append too.
    The inbox's append makes the row when the session has none."""
    table = cast(Table, AgentSteps.__table__)
    cursors = cast(Table, AgentStepCursors.__table__)
    # Named apart from the cursor row's columns: a parameter named for a
    # column of the UPDATE's table would be read as a value to SET.
    org_id = bindparam("tenant", type_=Uuid())
    agent_session_id = bindparam("session", type_=Uuid())
    incoming = (
        func.unnest(
            *(
                bindparam(name, type_=ARRAY(Text() if name in JSON_COLUMNS else table.c[name].type))
                for name in WRITTEN
            )
        )
        .table_valued(*WRITTEN, with_ordinality="ordinal")
        .render_derived(name="incoming")
    )
    # A step already appended to this session is left out, and takes no
    # number. An id another session or another tenant holds is not left out:
    # the insert meets the primary key, and the whole call rolls back. The
    # check names the session and the id alone, so it probes the primary
    # key: with `org_id` leading it, the planner walks the session's whole
    # history on the unique (org_id, agent_session_id, seq) instead. The tenant is
    # the policy's to fence, and the insert's.
    fresh = (
        select(
            *(incoming.c[name] for name in WRITTEN),
            func.row_number().over(order_by=incoming.c.ordinal).label("rank"),
        )
        .where(
            ~exists().where(
                table.c.agent_session_id == agent_session_id,
                table.c.id == incoming.c.id,
            )
        )
        .cte("fresh")
    )
    size = select(func.count()).select_from(fresh).scalar_subquery()
    if fenced:
        epoch = bindparam("held_epoch", type_=BigInteger())
        taken = (
            update(cursors)
            .where(
                cursors.c.org_id == org_id,
                cursors.c.agent_session_id == agent_session_id,
                cursors.c.epoch == epoch,
                size > 0,
            )
            .values(head=cursors.c.head + size)
            .returning(cursors.c.head)
            .cte("taken")
        )
    else:
        wanted = (
            select(org_id, agent_session_id, func.count(), literal(0, BigInteger()))
            .select_from(fresh)
            .having(func.count() > 0)
        )
        reserve = pg_insert(cursors).from_select(
            ["org_id", "agent_session_id", "head", "epoch"], wanted
        )
        taken = (
            reserve.on_conflict_do_update(
                index_elements=[cursors.c.org_id, cursors.c.agent_session_id],
                set_={"head": cursors.c.head + reserve.excluded.head},
            )
            .returning(cursors.c.head)
            .cte("taken")
        )
    written = [
        cast_(fresh.c[name], JSONB) if name in JSON_COLUMNS else fresh.c[name] for name in WRITTEN
    ]
    return (
        insert(table)
        .from_select(
            [*WRITTEN, *STORAGE_COLUMNS],
            select(
                *written, org_id, agent_session_id, taken.c.head - size + fresh.c.rank
            ).select_from(fresh.join(taken, true())),
        )
        .returning(*table.c)
    )


APPEND_FENCED = _append_statement(fenced=True)
APPEND_INPUTS = _append_statement(fenced=False)


def _begin_statement(org_id: UUID, agent_session_id: UUID) -> Any:
    """The epoch's compare-and-set, in one statement on the cursor row: the
    epoch moves one up from whatever the row holds when the statement takes
    its lock, so no two runs leave with one epoch."""
    stmt = pg_insert(AgentStepCursors).values(
        org_id=org_id, agent_session_id=agent_session_id, head=0, epoch=1
    )
    return stmt.on_conflict_do_update(
        index_elements=[AgentStepCursors.org_id, AgentStepCursors.agent_session_id],
        set_={"epoch": AgentStepCursors.epoch + 1},
    ).returning(AgentStepCursors.epoch)


class AgentStepStoragePostgresImpl(PgStorageBase, AgentStepStorageInterface):
    async def begin_run(self, org_id: UUID, agent_session_id: UUID) -> int:
        stmt = _begin_statement(org_id, agent_session_id)
        async with self._session_for(AgentStepCursors, org_id=org_id) as session:
            epoch = (await session.execute(stmt)).scalar_one()
            await session.commit()
            return int(epoch)

    async def append_agent_steps(
        self, org_id: UUID, agent_session_id: UUID, epoch: int, steps: Sequence[AgentStep]
    ) -> tuple[AgentStep, ...]:
        return await self._append_all(org_id, agent_session_id, steps, epoch)

    async def append_inputs(
        self, org_id: UUID, agent_session_id: UUID, steps: Sequence[AgentStep]
    ) -> tuple[AgentStep, ...]:
        return await self._append_all(org_id, agent_session_id, steps, None)

    async def _append_all(
        self, org_id: UUID, agent_session_id: UUID, steps: Sequence[AgentStep], epoch: int | None
    ) -> tuple[AgentStep, ...]:
        """The append under the epoch, or the inbox's when `epoch` is None,
        and the account of every step it left out: stored already in this
        session, held by another session or another tenant, or refused by
        the cursor row."""
        batch = list(steps)
        refusal = batch_refusal(agent_session_id, batch, inputs_only=epoch is None)
        if refusal is not None:
            raise ValidationFailed(refusal)
        stored: dict[UUID, AgentStep] = {}
        while pending := [step for step in batch if step.id not in stored]:
            try:
                stored.update(await self._append(org_id, agent_session_id, pending, epoch))
            except IntegrityError as error:
                constraint = violated_constraint(error)
                if constraint != cast(Table, AgentSteps.__table__).primary_key.name:
                    raise UniqueKeyTaken(
                        f"steps of {agent_session_id}: {constraint or 'a unique key'} is taken"
                    ) from error
                # An id of the batch was appended since the statement read the
                # history: the same append sent twice at once. The rollback
                # returned every number, and the retry leaves out what is
                # stored now. An id that stays out of reach is another tenant's.
                found = self._of_session(
                    agent_session_id, await self._read_ids(org_id, [step.id for step in pending])
                )
                if not found:
                    raise TenantMismatch(
                        f"steps {[str(step.id) for step in pending]}: an id is not in {org_id}"
                    ) from error
                stored.update(found)
                continue
            skipped = [step.id for step in pending if step.id not in stored]
            if skipped:
                found = await self._read_ids(org_id, skipped)
                stored.update(self._of_session(agent_session_id, found))
                if any(agent_step_id not in found for agent_step_id in skipped):
                    # Not stored, and not appended now: the cursor row was not
                    # at the run's epoch, so it handed out no number.
                    raise StaleWriter(f"session {agent_session_id} is not held at epoch {epoch}")
            break
        return tuple(stored[step.id] for step in batch)

    @staticmethod
    def _of_session(agent_session_id: UUID, found: dict[UUID, AgentStep]) -> dict[UUID, AgentStep]:
        """The stored steps of this session; an id another session of the
        tenant holds refuses the append, since it is not the same step."""
        elsewhere = [
            step.id for step in found.values() if step.agent_session_id != agent_session_id
        ]
        if elsewhere:
            raise UniqueKeyTaken(f"step {elsewhere[0]} is another session's")
        return found

    async def _append(
        self, org_id: UUID, agent_session_id: UUID, steps: list[AgentStep], epoch: int | None
    ) -> dict[UUID, AgentStep]:
        """The statement for these steps: the new ones take the next numbers
        from the cursor row and are written with them, and come back by id."""
        rows = [to_values(step, AgentSteps) for step in steps]
        params: dict[str, object] = {name: [row[name] for row in rows] for name in WRITTEN}
        for name in JSON_COLUMNS:
            params[name] = [json.dumps(row[name]) for row in rows]
        params["tenant"] = org_id
        params["session"] = agent_session_id
        statement = APPEND_INPUTS
        if epoch is not None:
            params["held_epoch"] = epoch
            statement = APPEND_FENCED
        async with self._session_for(AgentSteps, org_id=org_id) as session:
            try:
                result = (await session.execute(statement, params)).all()
                appended = {row.id: to_model(row, AgentStep) for row in result}
                await session.commit()
            except IntegrityError:
                # The rollback returns the numbers with it, so the history stays gapless.
                await session.rollback()
                raise
            return appended

    async def _read_ids(self, org_id: UUID, agent_step_ids: list[UUID]) -> dict[UUID, AgentStep]:
        stmt = select(AgentSteps).where(
            AgentSteps.org_id == org_id, AgentSteps.id.in_(agent_step_ids)
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            return {
                row.id: to_model(row, AgentStep) for row in (await session.execute(stmt)).scalars()
            }

    async def read_agent_steps(
        self, org_id: UUID, agent_session_id: UUID, after_seq: int, limit: int
    ) -> list[AgentStep]:
        stmt = (
            select(AgentSteps)
            .where(
                AgentSteps.org_id == org_id,
                AgentSteps.agent_session_id == agent_session_id,
                AgentSteps.seq > after_seq,
            )
            .order_by(AgentSteps.seq)
            .limit(limit)
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            return [to_model(row, AgentStep) for row in (await session.execute(stmt)).scalars()]

    async def purge_history(self, org_id: UUID, agent_session_id: UUID, limit: int) -> int:
        # The steps first, a batch on the index that (org_id, agent_session_id)
        # leads, then the cursor row in the same transaction once none is
        # left, so a history is never left numbered from nothing. The funnel
        # holds the tenant's purge lock, so a second purge waits for this one
        # and then counts what is left (`hold_purge`).
        batch = (
            select(AgentSteps.id)
            .where(AgentSteps.org_id == org_id, AgentSteps.agent_session_id == agent_session_id)
            .limit(limit)
        )
        steps = delete(AgentSteps).where(AgentSteps.org_id == org_id, AgentSteps.id.in_(batch))
        cursor = delete(AgentStepCursors).where(
            AgentStepCursors.org_id == org_id, AgentStepCursors.agent_session_id == agent_session_id
        )
        async with self._purge_session_for(AgentSteps, org_id=org_id) as session:
            purged = deleted(await session.execute(steps))
            if purged < limit:
                purged += deleted(await session.execute(cursor))
            await session.commit()
            return purged

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        # As `purge_history`, across the tenant's sessions: its steps by the
        # index org_id leads, then its cursor rows once no step is left.
        batch = select(AgentSteps.id).where(AgentSteps.org_id == org_id).limit(limit)
        steps = delete(AgentSteps).where(AgentSteps.org_id == org_id, AgentSteps.id.in_(batch))
        async with self._purge_session_for(AgentSteps, org_id=org_id) as session:
            purged = deleted(await session.execute(steps))
            if purged < limit:
                sessions = (
                    select(AgentStepCursors.agent_session_id)
                    .where(AgentStepCursors.org_id == org_id)
                    .limit(limit - purged)
                )
                cursors = delete(AgentStepCursors).where(
                    AgentStepCursors.org_id == org_id,
                    AgentStepCursors.agent_session_id.in_(sessions),
                )
                purged += deleted(await session.execute(cursors))
            await session.commit()
            return purged

    async def read_cursor(self, org_id: UUID, agent_session_id: UUID) -> AgentStepCursor:
        stmt = select(AgentStepCursors.head, AgentStepCursors.epoch).where(
            AgentStepCursors.org_id == org_id, AgentStepCursors.agent_session_id == agent_session_id
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            found = (await session.execute(stmt)).one_or_none()
        if found is None:
            return AgentStepCursor()
        return AgentStepCursor(head=int(found.head), epoch=int(found.epoch))
