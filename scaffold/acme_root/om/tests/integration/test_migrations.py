"""Every role's migrated schema agrees with the ORM metadata, the latest
revision of every role downgrades and upgrades again, a session row the
previous release writes takes its private-data default, a usage record written
before its time to the first token reads it null, the logins are safe to
make twice and stand on every database a role lives on, a migration behind a
held lock gives up within its bound, a stamp writes another checkout's heads
and back, and a data migration passes the fence it runs under and fails when
it misses rows."""

import asyncio
import time
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import create_async_engine

from acme.om.base import new_id
from acme.om.change_events.storage.impl.postgres import ChangeEventStoragePostgresImpl
from acme.om.storage.impl.pg_base import LoginSessions
from acme.om.storage.logins import MIGRATION_LOGIN, PURGE_LOGIN, RUNTIME_LOGIN, SYSTEM_LOGIN
from acme.om.storage.migrate import (
    MIGRATIONS_DIR,
    RUN_AGAIN,
    VERSION_TABLE,
    backfill,
    check,
    downgrade,
    ensure_logins_everywhere,
    head,
    main,
    upgrade,
)
from acme.om.storage.roles import DatabaseRole
from acme.om.storage.settings import MigrationSettings

from contracts.change_event_storage import make_change_event

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("role", list(DatabaseRole))
async def test_orm_and_schema_agree(migrated: dict[DatabaseRole, str], role: DatabaseRole) -> None:
    assert await check(role, migrated[role]) == []


@pytest.mark.parametrize("role", list(DatabaseRole))
async def test_latest_revision_round_trips(
    migrated: dict[DatabaseRole, str], role: DatabaseRole
) -> None:
    if head(role) is None:
        pytest.skip(f"role {role.value} has no migrations yet")
    await downgrade(role, migrated[role], "-1")
    await upgrade(role, migrated[role])
    assert await check(role, migrated[role]) == []


async def test_a_session_row_written_without_its_private_data_mark_holds_private_data(
    migrated: dict[DatabaseRole, str],
) -> None:
    """The previous release writes no `holds_private`, during a roll and
    after a rollback: the column keeps its default, so such a row is taken
    to hold private data rather than refused."""
    engine = create_async_engine(migrated[DatabaseRole.CORE])
    try:
        async with engine.connect() as connection:
            default = await connection.scalar(
                text(
                    "SELECT column_default FROM information_schema.columns"
                    " WHERE table_schema = 'core' AND table_name = 'agent_sessions'"
                    " AND column_name = 'holds_private'"
                )
            )
    finally:
        await engine.dispose()
    assert default == "true"


FIRST_TOKEN = "202611241949"
"""The activity revision that adds a usage record's time to the first token."""

A_RECORD = (
    "INSERT INTO activity.usage_records (id, org_id, created_at, spend_hold_id,"
    " agent_session_id, tree_id, loop_id, agent_step_id, agent_kind, kind_version, role,"
    " provider, model, input_tokens, cache_read_tokens, cache_write_tokens, output_tokens,"
    " thinking_tokens, cost_micros, latency_ms, settled_whole) VALUES (:id, :org, now(), :id,"
    " :id, :id, :id, :id, 'assistant', 1, 'main', 'anthropic', 'claude-sonnet', 10, 0, 0, 5,"
    " 0, 300, 1000, false)"
)


async def on_a_record(url: str, org: UUID, record: UUID, sql: str) -> object:
    """One statement on a usage record under its tenant's fence, as the
    migration login, which owns the table; the one value it reads, if any."""
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org)}
            )
            result = await connection.execute(text(sql), {"id": record, "org": org})
            return result.scalar_one() if result.returns_rows else None
    finally:
        await engine.dispose()


async def test_a_usage_record_written_before_the_first_token_column_reads_null(
    migrated: dict[DatabaseRole, str],
) -> None:
    """A database at the activity chain's head before the column, holding a
    record the release before wrote: the column comes in null on that row,
    goes out, and comes in again."""
    url = migrated[DatabaseRole.ACTIVITY]
    org, record = new_id(), new_id()
    read = "SELECT first_token_ms FROM activity.usage_records WHERE id = :id"
    await downgrade(DatabaseRole.ACTIVITY, url, f"{FIRST_TOKEN}-1")
    try:
        await on_a_record(url, org, record, A_RECORD)
        await upgrade(DatabaseRole.ACTIVITY, url)
        assert await on_a_record(url, org, record, read) is None
        await downgrade(DatabaseRole.ACTIVITY, url, f"{FIRST_TOKEN}-1")
        await upgrade(DatabaseRole.ACTIVITY, url)
        assert await on_a_record(url, org, record, read) is None
        assert await check(DatabaseRole.ACTIVITY, url) == []
    finally:
        await upgrade(DatabaseRole.ACTIVITY, url)
        delete = "DELETE FROM activity.usage_records WHERE id = :id"
        await on_a_record(url, org, record, delete)


async def test_ensure_logins_runs_again_on_a_migrated_database(
    migration_settings: MigrationSettings, migrated: dict[DatabaseRole, str]
) -> None:
    """The deploy runs it before every migrate, so the second run over a
    database it already shaped changes nothing and fails nothing."""
    settings = migration_settings
    await ensure_logins_everywhere(settings.master_databases(), settings.login_passwords())
    for role in DatabaseRole:
        assert await check(role, migrated[role]) == []


async def test_each_database_holds_the_logins_and_its_own_roles_schemas(
    migration_settings: MigrationSettings, migrated: dict[DatabaseRole, str]
) -> None:
    """A login, a grant, and a schema's owner live on one instance, so every
    database a role lives on carries its own: the four logins, none a
    superuser or BYPASSRLS, and the schema of each role there owned by the
    migration login and open to the serving logins. The cloud's one database
    holds all four; the local stack's four hold one role each, and none
    holds another's schema."""
    logins = sorted((MIGRATION_LOGIN, PURGE_LOGIN, RUNTIME_LOGIN, SYSTEM_LOGIN))
    every_role = [role.value for role in DatabaseRole]
    for url, roles in migration_settings.master_databases().items():
        engine = create_async_engine(url)
        try:
            async with engine.connect() as connection:
                found = await connection.execute(
                    text(
                        "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles"
                        " WHERE rolname = ANY(:logins) ORDER BY rolname"
                    ),
                    {"logins": logins},
                )
                assert [tuple(row) for row in found] == [(login, False, False) for login in logins]
                owners = await connection.execute(
                    text(
                        "SELECT nspname, pg_get_userbyid(nspowner) FROM pg_namespace"
                        " WHERE nspname = ANY(:schemas)"
                    ),
                    {"schemas": every_role},
                )
                assert dict(tuple(row) for row in owners) == {
                    role.value: MIGRATION_LOGIN for role in roles
                }
                for role in roles:
                    for login in (RUNTIME_LOGIN, SYSTEM_LOGIN):
                        usage = await connection.execute(
                            text("SELECT has_schema_privilege(:login, :schema, 'USAGE')"),
                            {"login": login, "schema": role.value},
                        )
                        assert usage.scalar_one(), f"{login} has no usage on {role.value}"
        finally:
            await engine.dispose()


async def test_a_migration_behind_a_held_lock_gives_up_within_its_bound(
    migrated: dict[DatabaseRole, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A transaction holds core's version table, which every run of core's
    chain reads first, so it stands for a lock on any table a migration
    touches. With the bound at one second, `migrate upgrade` gives up after
    that second, applies nothing, and exits RUN_AGAIN, which the deploy's
    pre-rollout task runs again on. Once the transaction ends, the same
    command applies the revision. Without the bound, the run waited for as
    long as the transaction stayed open."""
    core = migrated[DatabaseRole.CORE]
    await downgrade(DatabaseRole.CORE, core, "-1")
    before = await on_core(core, f"SELECT version_num FROM core.{VERSION_TABLE}")
    monkeypatch.setenv("ACME_DATABASE_MIGRATION_LOCK_TIMEOUT_SECONDS", "1")
    holder = create_async_engine(core)
    try:
        async with holder.begin() as held:
            await held.execute(text(f"LOCK TABLE core.{VERSION_TABLE} IN ACCESS EXCLUSIVE MODE"))
            started = time.monotonic()
            code = await asyncio.to_thread(main, ["upgrade", "--role", "core"])
            waited = time.monotonic() - started
    finally:
        await holder.dispose()
    assert code == RUN_AGAIN
    # The second of waiting, and what it costs to open a connection and read
    # the chain around it.
    assert 1.0 <= waited < 3.0
    assert "core: a lock was not granted within 1 s" in capsys.readouterr().err
    assert await on_core(core, f"SELECT version_num FROM core.{VERSION_TABLE}") == before
    assert await asyncio.to_thread(main, ["upgrade", "--role", "core"]) == 0
    assert await check(DatabaseRole.CORE, core) == []


async def test_a_stamp_writes_another_checkouts_heads_and_back(
    migrated: dict[DatabaseRole, str], checkout_ahead: tuple[Path, str]
) -> None:
    """How the release before runs its own suite on this schema: each role's
    record becomes the head of that checkout's chain, a revision this chain
    does not hold included, with nothing applied; stamped back to this
    checkout, every record is this chain's head again."""
    checkout, ahead = checkout_ahead
    record = f"SELECT version_num FROM core.{VERSION_TABLE}"
    try:
        assert await asyncio.to_thread(main, ["stamp", "--all", "--heads-of", str(checkout)]) == 0
        assert await on_core(migrated[DatabaseRole.CORE], record) == [(ahead,)]
        assert await check(DatabaseRole.CORE, migrated[DatabaseRole.CORE]) == []
    finally:
        here = str(MIGRATIONS_DIR.parents[1])
        assert await asyncio.to_thread(main, ["stamp", "--all", "--heads-of", here]) == 0
    assert await on_core(migrated[DatabaseRole.CORE], record) == [(head(DatabaseRole.CORE),)]


async def seed_two_tenants(pg_sessions: LoginSessions) -> None:
    events = ChangeEventStoragePostgresImpl(pg_sessions)
    for org in (new_id(), new_id()):
        await events.append_change_events(org, [make_change_event(org)])


def forced(connection: Connection) -> bool:
    return connection.exec_driver_sql(
        "SELECT relforcerowsecurity FROM pg_class WHERE oid = 'activity.change_events'::regclass"
    ).scalar_one()


COUNT = "SELECT count(*) FROM activity.change_events"
UPDATE = "UPDATE activity.change_events SET kind = kind"


async def test_a_backfill_under_the_fence_touches_every_tenant(
    pg_sessions: LoginSessions, migrated: dict[DatabaseRole, str]
) -> None:
    """A migration names no tenant, and FORCE binds the migration login that
    owns the table, so a bare UPDATE matches no row and succeeds; its count
    says zero too, and zero equals zero. Seeded with two tenants' rows, the
    backfill counts and touches both, and the fence is back after it."""
    await seed_two_tenants(pg_sessions)
    engine = create_async_engine(migrated[DatabaseRole.ACTIVITY])
    try:
        async with engine.connect() as connection:

            def bare(sync: Connection) -> tuple[int, int]:
                expected = sync.exec_driver_sql(COUNT).scalar_one()
                return expected, sync.exec_driver_sql(UPDATE).rowcount

            assert await connection.run_sync(bare) == (0, 0)
            await connection.rollback()

            def fenced(sync: Connection) -> tuple[int, bool]:
                touched = backfill(sync, DatabaseRole.ACTIVITY, "change_events", COUNT, UPDATE)
                return touched, forced(sync)

            assert await connection.run_sync(fenced) == (2, True)
            await connection.rollback()
    finally:
        await engine.dispose()


async def test_a_backfill_that_misses_rows_fails_and_keeps_the_fence(
    pg_sessions: LoginSessions, migrated: dict[DatabaseRole, str]
) -> None:
    await seed_two_tenants(pg_sessions)
    engine = create_async_engine(migrated[DatabaseRole.ACTIVITY])
    try:
        async with engine.connect() as connection:
            partial = (
                "UPDATE activity.change_events SET kind = kind WHERE seq = 1"
                " AND org_id = (SELECT org_id FROM activity.change_events ORDER BY org_id LIMIT 1)"
            )
            with pytest.raises(RuntimeError, match="touched 1 rows of 2"):
                await connection.run_sync(
                    lambda sync: backfill(
                        sync, DatabaseRole.ACTIVITY, "change_events", COUNT, partial
                    )
                )
            await connection.rollback()
            assert await connection.run_sync(forced) is True
    finally:
        await engine.dispose()


async def on_core(url: str, sql: str) -> list[tuple[object, ...]]:
    """One statement on the core role, as the migration login, which owns the
    version table the lock test reads."""
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            result = await connection.exec_driver_sql(sql)
            return [tuple(row) for row in result] if result.returns_rows else []
    finally:
        await engine.dispose()
