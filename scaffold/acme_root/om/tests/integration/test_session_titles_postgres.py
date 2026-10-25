"""A session's title over Postgres, read the way a database reader reads
it: the rows of the sessions and of their privacy, under the tenant's own
scope (ADR 1042). A title's row holds none of its words; a title stored in
the clear, as a release before titles were sealed wrote it, is sealed in
place by the sweep and reads back the same; and the sweep's write misses a
row that moved since its read."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Update, text
from sqlalchemy.ext.asyncio import AsyncSession

from acme.infra.impl.local import InfraLocalImpl
from acme.om.agent_root import AgentRootOptions
from acme.om.agent_sessions.types.agent_session import AgentSession, Title
from acme.om.agent_steps.types.content import ContentState
from acme.om.base import new_id
from acme.om.context import AppContext, AppType, RequestContext, TenantContext
from acme.om.root import Managers, build_managers
from acme.om.storage.impl.pg_base import LoginSessions, set_scope
from acme.om.storage.impl.postgres import StoragePostgresImpl
from acme.om.storage.roles import DatabaseRole
from acme.om.storage.settings import MigrationSettings

from contracts.agent_session_storage import make_agent_session
from contracts.agent_step_storage import make_message
from contracts.tools import stand_ins

pytestmark = pytest.mark.integration

APP = AppContext(type=AppType.PORTAL, version="portal@test")
TOOLS = stand_ins("read_log", "run_tests")


@pytest.fixture
async def storage(
    migration_settings: MigrationSettings, migrated: object
) -> AsyncIterator[StoragePostgresImpl]:
    root = StoragePostgresImpl(
        migration_settings.role_urls(),
        migration_settings.role_pools(),
        system_urls=migration_settings.system_role_urls(),
    )
    yield root
    await root.close()


@pytest.fixture
def managers(storage: StoragePostgresImpl, tmp_path: Path) -> Managers:
    return build_managers(
        storage, InfraLocalImpl(tmp_path), agent_root_options=AgentRootOptions(tool_catalog=TOOLS)
    )


async def an_org(managers: Managers) -> TenantContext:
    slug = f"ajax-{new_id().hex[-8:]}"
    owner, _ = await managers.tenancy.bootstrap(
        RequestContext(request_id=new_id(), app=APP), "Ajax", slug, f"ann-{slug}@x.test", "Ann"
    )
    return owner


def marker() -> str:
    return f"zq-{new_id().hex[-12:]}"


async def query(
    pg_sessions: LoginSessions, org_id: UUID, statement: str, **values: Any
) -> list[dict[str, Any]]:
    """What a reader of the core database sees, under the tenant's scope."""
    async with pg_sessions[DatabaseRole.CORE]() as session:
        await set_scope(session, org_id, None, None)
        result = await session.execute(text(statement), {"org": org_id, **values})
        rows = [dict(row) for row in result.mappings()] if result.returns_rows else []
        await session.commit()
        return rows


HOLDING = (
    "SELECT"
    " (SELECT count(*) FROM core.agent_sessions s WHERE s.org_id = :org"
    "   AND (s::text LIKE :like OR position(convert_to(:mark, 'UTF8') IN coalesce(s.title_sealed, ''::bytea)) > 0))"
    " AS sessions,"
    " (SELECT count(*) FROM core.agent_session_privacy r WHERE r.org_id = :org"
    "   AND (r::text LIKE :like OR position(convert_to(:mark, 'UTF8') IN coalesce(r.title_key, ''::bytea)) > 0))"
    " AS records"
)
"""How many rows of the tenant's sessions and records hold the marker, as
text or as bytes."""


async def holding(pg_sessions: LoginSessions, org_id: UUID, mark: str) -> dict[str, Any]:
    (found,) = await query(pg_sessions, org_id, HOLDING, mark=mark, like=f"%{mark}%")
    return found


def titled(ctx: TenantContext, title: str) -> AgentSession:
    return make_agent_session().model_copy(
        update={"title": Title(text=title), "created_by": ctx.user_id}
    )


async def test_a_titles_row_holds_none_of_its_words_and_the_title_opens(
    managers: Managers, pg_sessions: LoginSessions
) -> None:
    ctx, mark = await an_org(managers), marker()
    made = await managers.agent_sessions.create_agent_session(ctx, titled(ctx, f"{mark} the total"))
    assert await holding(pg_sessions, ctx.org_id, mark) == {"sessions": 0, "records": 0}
    (row,) = await query(
        pg_sessions,
        ctx.org_id,
        "SELECT title, octet_length(title_sealed) AS sealed FROM core.agent_sessions"
        " WHERE org_id = :org AND id = :id",
        id=made.id,
    )
    assert row["title"] == "(sealed)" and row["sealed"] > len(f"{mark} the total")
    read = await managers.agent_sessions.get_agent_session(ctx, made.id)
    (opened,) = await managers.agent_sessions.open_titles(ctx, [read])
    assert opened.title.said() == f"{mark} the total"


async def as_the_release_before_wrote_it(
    pg_sessions: LoginSessions, ctx: TenantContext, session: AgentSession, title: str
) -> None:
    """The row a release before titles were sealed wrote: its title in the
    clear, no sealed title, and no title key on any record."""
    await query(
        pg_sessions,
        ctx.org_id,
        "UPDATE core.agent_sessions SET title = :title, title_sealed = NULL"
        " WHERE org_id = :org AND id = :id",
        id=session.id,
        title=title,
    )
    await query(
        pg_sessions,
        ctx.org_id,
        "DELETE FROM core.agent_session_privacy WHERE org_id = :org AND agent_session_id = :id",
        id=session.id,
    )


async def test_a_title_in_the_clear_is_sealed_in_place_and_reads_back_the_same(
    managers: Managers, pg_sessions: LoginSessions
) -> None:
    """Each title reads back as it was, before the sweep as after it; after
    it no row holds a title in the clear, and a revoked session's title is
    gone. A pass that finds none takes none."""
    ctx, mark = await an_org(managers), marker()
    said = [f"{mark} title {n}" for n in range(3)]
    made = [await managers.agent_sessions.create_agent_session(ctx, titled(ctx, "x")) for _ in said]
    for session, title in zip(made, said, strict=True):
        await as_the_release_before_wrote_it(pg_sessions, ctx, session, title)
    revoked = made[2]
    await managers.privacy.revoke_key(ctx, revoked.id)
    before = await managers.agent_sessions.open_titles(
        ctx, [await managers.agent_sessions.get_agent_session(ctx, s.id) for s in made]
    )
    assert [s.title for s in before] == [Title(text=title) for title in said]
    assert (await holding(pg_sessions, ctx.org_id, mark))["sessions"] == 3
    while await managers.agent_sessions.seal_titles_across_tenants():
        pass
    assert await holding(pg_sessions, ctx.org_id, mark) == {"sessions": 0, "records": 0}
    (clear,) = await query(
        pg_sessions,
        ctx.org_id,
        "SELECT count(*) AS n FROM core.agent_sessions WHERE org_id = :org AND title_sealed IS NULL",
    )
    assert clear["n"] == 0
    after = await managers.agent_sessions.open_titles(
        ctx, [await managers.agent_sessions.get_agent_session(ctx, s.id) for s in made]
    )
    assert [s.title for s in after[:2]] == [Title(text=title) for title in said[:2]]
    assert after[2].title.state is ContentState.ABSENT
    assert await managers.agent_sessions.seal_titles_across_tenants() == 0


async def test_the_sweeps_write_misses_a_row_that_moved_since_its_read(
    managers: Managers, storage: StoragePostgresImpl, pg_sessions: LoginSessions
) -> None:
    """A writer that read the clear title, as the release before writes every
    column, lands first: the sweep's write is refused by the version, and the
    next pass seals the row as it now stands."""
    ctx, mark = await an_org(managers), marker()
    made = await managers.agent_sessions.create_agent_session(ctx, titled(ctx, "x"))
    await as_the_release_before_wrote_it(pg_sessions, ctx, made, mark)
    sessions = storage.get_agent_session_storage()
    found = await sessions.read_clear_titles(1000)
    read = next(s for _, s in found if s.id == made.id)
    await query(
        pg_sessions,
        ctx.org_id,
        "UPDATE core.agent_sessions SET version = version + 1 WHERE org_id = :org AND id = :id",
        id=made.id,
    )
    resealed = read.model_copy(
        update={"title": Title(state=ContentState.ABSENT), "version": read.version + 1}
    )
    assert not await sessions.seal_title(ctx.org_id, resealed, read.version)
    assert (await holding(pg_sessions, ctx.org_id, mark))["sessions"] == 1
    while await managers.agent_sessions.seal_titles_across_tenants():
        pass
    sealed = await managers.agent_sessions.get_agent_session(ctx, made.id)
    assert sealed.version == read.version + 2
    (opened,) = await managers.agent_sessions.open_titles(ctx, [sealed])
    assert opened.title.said() == mark


async def test_a_revocation_that_left_the_title_key_still_leaves_the_title_absent(
    managers: Managers, pg_sessions: LoginSessions
) -> None:
    """A release before titles were sealed revokes without clearing the
    title key: the title reads as absent all the same, nothing seals under
    that key again, and the next revocation clears it."""
    ctx = await an_org(managers)
    made = await managers.agent_sessions.create_agent_session(ctx, titled(ctx, "left behind"))
    await query(
        pg_sessions,
        ctx.org_id,
        "UPDATE core.agent_session_privacy SET revoked_at = now(), revoked_by = :by"
        " WHERE org_id = :org AND agent_session_id = :id",
        id=made.id,
        by=ctx.user_id,
    )
    (shown,) = await managers.agent_sessions.open_titles(ctx, [made])
    assert shown.title.state is ContentState.ABSENT
    await managers.privacy.revoke_key(ctx, made.id)
    (row,) = await query(
        pg_sessions,
        ctx.org_id,
        "SELECT title_key FROM core.agent_session_privacy WHERE org_id = :org AND agent_session_id = :id",
        id=made.id,
    )
    assert row["title_key"] is None


async def test_a_revocation_that_fails_part_way_leaves_neither_half_done(
    managers: Managers, pg_sessions: LoginSessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The title key is cleared in the commit that destroys the data keys: a
    revocation whose destroy fails lands neither, and the next one lands
    both."""
    ctx = await an_org(managers)
    made = await managers.agent_sessions.create_agent_session(ctx, titled(ctx, "kept whole"))
    await managers.agent_steps.append_inputs(ctx, made.id, [make_message(made.id)])
    execute = AsyncSession.execute

    async def failing(self: AsyncSession, statement: Any, *args: Any, **kwargs: Any) -> Any:
        table = getattr(getattr(statement, "table", None), "name", None)
        if isinstance(statement, Update) and table == "agent_session_keys":
            raise RuntimeError("the destroy fails")
        return await execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", failing)
    with pytest.raises(RuntimeError, match="the destroy fails"):
        await managers.privacy.revoke_key(ctx, made.id)
    monkeypatch.setattr(AsyncSession, "execute", execute)
    state = (
        "SELECT r.revoked_at, r.title_key IS NOT NULL AS titled,"
        " (SELECT count(*) FROM core.agent_session_keys k WHERE k.org_id = r.org_id"
        "  AND k.agent_session_id = r.agent_session_id AND k.wrapped IS NOT NULL) AS live"
        " FROM core.agent_session_privacy r WHERE r.org_id = :org AND r.agent_session_id = :id"
    )
    (before,) = await query(pg_sessions, ctx.org_id, state, id=made.id)
    assert (before["revoked_at"], before["titled"], before["live"]) == (None, True, 1)
    (shown,) = await managers.agent_sessions.open_titles(ctx, [made])
    assert shown.title.said() == "kept whole"
    await managers.privacy.revoke_key(ctx, made.id)
    (after,) = await query(pg_sessions, ctx.org_id, state, id=made.id)
    assert after["revoked_at"] is not None and (after["titled"], after["live"]) == (False, 0)
