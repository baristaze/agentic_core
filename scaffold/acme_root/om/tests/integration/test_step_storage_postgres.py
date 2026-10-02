"""The step storage contract over Postgres, and what only the database holds:
the serving logins cannot rewrite or remove a step, and an append in flight
holds the session's cursor row to its commit."""

import asyncio
from uuid import UUID

import pytest
from contracts.racing import race
from contracts.step_storage import StepStorageContract, make_message
from sqlalchemy import TextClause, text, update
from sqlalchemy.exc import DBAPIError

from acme.om.base import EMPTY_UUID, new_id
from acme.om.exceptions import StaleWriter
from acme.om.steps.storage import StepStorageInterface
from acme.om.steps.storage.impl.postgres import StepStoragePostgresImpl
from acme.om.steps.storage.tables.step_cursors import StepCursors
from acme.om.storage.impl.pg_base import LoginSessions, SessionFactory, set_scope
from acme.om.storage.logins import RUNTIME_LOGIN, SYSTEM_LOGIN
from acme.om.storage.migrate import ensure_logins_at
from acme.om.storage.roles import DatabaseRole
from acme.om.storage.settings import MigrationSettings

pytestmark = pytest.mark.integration


class TestStepStoragePostgres(StepStorageContract):
    @pytest.fixture
    def storage(self, pg_sessions: LoginSessions) -> StepStorageInterface:
        return StepStoragePostgresImpl(pg_sessions)

    async def test_concurrent_appends_meet_on_the_cursor_and_leave_no_gap(
        self, storage: StepStorageInterface
    ) -> None:
        """The contract's race, held to have raced: over the engine the
        appends are inside the statement at once, a run's and the inbox's
        mixed, and still leave with 1..N."""
        org, session, n = new_id(), new_id(), 48
        epoch = await storage.begin_run(org, session)

        async def one(by_run: bool) -> int:
            step = make_message(session)
            if by_run:
                (appended,) = await storage.append_steps(org, session, epoch, [step])
            else:
                (appended,) = await storage.append_inputs(org, session, [step])
            return appended.seq

        run = await race(*(one(i % 3 != 0) for i in range(n)))
        assert run.overlapped, run.summary()
        assert sorted(run.outcomes) == list(range(1, n + 1)), run.summary()
        history = await storage.read_steps(org, session, 0, n * 2)
        assert [s.seq for s in history] == list(range(1, n + 1))

    async def test_a_number_commits_only_after_the_numbers_below_it(
        self, storage: StepStorageInterface, pg_sessions: LoginSessions
    ) -> None:
        """An append in flight holds the cursor row from its statement to
        its commit, so the next append waits behind it and takes the number
        after it. Here the one in flight is spelled out by hand: it moved the
        head and has not committed."""
        org, session = new_id(), new_id()
        epoch = await storage.begin_run(org, session)
        await storage.append_steps(org, session, epoch, [make_message(session)])
        async with pg_sessions[DatabaseRole.ACTIVITY]() as in_flight:
            await set_scope(in_flight, org, None, None)
            await in_flight.execute(
                update(StepCursors)
                .where(StepCursors.org_id == org, StepCursors.session_id == session)
                .values(head=StepCursors.head + 1)
            )
            behind = asyncio.create_task(
                storage.append_steps(org, session, epoch, [make_message(session)])
            )
            await asyncio.sleep(0.3)
            assert not behind.done(), "the next append did not wait on the cursor"
            await in_flight.commit()
        (appended,) = await behind
        assert appended.seq == 3

    async def test_a_run_that_begins_while_an_append_waits_fences_it(
        self, storage: StepStorageInterface, pg_sessions: LoginSessions
    ) -> None:
        """The append waits on the cursor row, and reads the row again once
        the holder commits: a run that began in that wait has moved the
        epoch, so the waiting append is refused and writes nothing."""
        org, session = new_id(), new_id()
        lost = await storage.begin_run(org, session)
        async with pg_sessions[DatabaseRole.ACTIVITY]() as next_run:
            await set_scope(next_run, org, None, None)
            await next_run.execute(
                update(StepCursors)
                .where(StepCursors.org_id == org, StepCursors.session_id == session)
                .values(epoch=StepCursors.epoch + 1)
            )
            behind = asyncio.create_task(
                storage.append_steps(org, session, lost, [make_message(session)])
            )
            await asyncio.sleep(0.3)
            assert not behind.done(), "the append did not wait on the cursor"
            await next_run.commit()
        with pytest.raises(StaleWriter):
            await behind
        assert await storage.read_steps(org, session, 0, 10) == []


UPDATE_STEP = text("UPDATE activity.steps SET type = type")
DELETE_STEP = text("DELETE FROM activity.steps")
UPDATE_CURSOR = text("UPDATE activity.step_cursors SET head = head RETURNING session_id")


async def refused(factory: SessionFactory, org_id: UUID, statement: TextClause) -> str:
    """The error a statement meets under the given scope, on a session of
    its own, since a refused statement ends its transaction."""
    async with factory() as session:
        await set_scope(session, org_id, None, None)
        with pytest.raises(DBAPIError) as error:
            await session.execute(statement)
        return str(error.value)


async def test_no_serving_login_rewrites_or_removes_a_step(
    pg_sessions: LoginSessions, migration_settings: MigrationSettings
) -> None:
    """A step is written once. The runtime login, under the step's own
    tenant, and the system login, under the system scope, may read and insert
    a step and are refused an UPDATE and a DELETE by the database itself:
    the refusal is the grant, ahead of any policy. The cursor row beside it
    stays theirs to move, which is what tells the refusal from a fence that
    refuses everything. A deploy makes the logins again before every
    migration, which grants DML on every table, so it runs here first: the
    history stays append-only after it."""
    settings = migration_settings
    await ensure_logins_at(settings.master_url(), settings.login_passwords())
    storage = StepStoragePostgresImpl(pg_sessions)
    org, session = new_id(), new_id()
    epoch = await storage.begin_run(org, session)
    (step,) = await storage.append_steps(org, session, epoch, [make_message(session)])

    for factory, scope in (
        (pg_sessions[DatabaseRole.ACTIVITY], org),
        (pg_sessions.system[DatabaseRole.ACTIVITY], EMPTY_UUID),
    ):
        for statement in (UPDATE_STEP, DELETE_STEP):
            error = await refused(factory, scope, statement)
            assert "permission denied for table steps" in error, error
        async with factory() as db:
            await set_scope(db, scope, None, None)
            assert (await db.execute(UPDATE_CURSOR)).scalars().all() == [session]
            await db.rollback()

    privilege = text("SELECT has_table_privilege(:login, 'activity.steps', :privilege)")
    held: set[tuple[str, str]] = set()
    async with pg_sessions[DatabaseRole.ACTIVITY]() as db:
        for login in (RUNTIME_LOGIN, SYSTEM_LOGIN):
            for kind in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                found = await db.execute(privilege, {"login": login, "privilege": kind})
                if found.scalar_one():
                    held.add((login, kind))
    assert held == {
        (RUNTIME_LOGIN, "SELECT"),
        (RUNTIME_LOGIN, "INSERT"),
        (SYSTEM_LOGIN, "SELECT"),
        (SYSTEM_LOGIN, "INSERT"),
    }
    assert await storage.read_steps(org, session, 0, 10) == [step]
