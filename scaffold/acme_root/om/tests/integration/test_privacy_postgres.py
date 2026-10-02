"""Sealing over Postgres, read the way a database reader reads it: the rows
of the history and of the session keys, under the runtime login.

The step storage contract runs over the sealing layer and over the router,
each over the Postgres history. Then what only the rows can show: a step's
content row holds ciphertext and nothing it said; a revocation empties
every version's wrapped copy and leaves the history's rows as they were; a
rotation re-wraps the keys and rewrites no content row; and a memory-only
session writes no content row, and its shape only when its policy keeps
it."""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from contracts.agent_session_storage import make_session
from contracts.step_storage import StepStorageContract, a_loop, make_message
from sqlalchemy import text

from acme.infra.impl.local import InfraLocalImpl
from acme.infra.keys.memory import KeyServiceMemoryImpl
from acme.om.base import new_id, utcnow
from acme.om.context import AppContext, AppType, RequestContext, TenantContext
from acme.om.privacy.impl.keys import SessionKeysImpl
from acme.om.privacy.impl.memory_only_steps import StepStorageShapeOnlyImpl
from acme.om.privacy.impl.routed_steps import StepStorageRoutedImpl
from acme.om.privacy.impl.sealed_steps import StepStorageSealedImpl
from acme.om.privacy.storage.impl.postgres import PrivacyStoragePostgresImpl
from acme.om.privacy.types.session_privacy import StorageMode, StoragePolicy
from acme.om.root import Managers, build_managers
from acme.om.steps.storage import StepStorageInterface
from acme.om.steps.storage.impl.memory import StepStorageMemoryImpl
from acme.om.steps.storage.impl.postgres import StepStoragePostgresImpl
from acme.om.steps.types.content import Children, ContentState
from acme.om.steps.types.step import Step
from acme.om.storage.impl.pg_base import LoginSessions, set_scope
from acme.om.storage.impl.postgres import StoragePostgresImpl
from acme.om.storage.roles import DatabaseRole
from acme.om.storage.settings import MigrationSettings

pytestmark = pytest.mark.integration

APP = AppContext(type=AppType.PORTAL, version="portal@test")
SAID = (
    "the weekly report is missing a total",
    "reading the import log",
    "the total is summed before the import ends",
    "200 lines",
    "plot.png",
)
"""What the loop the contract writes says, and no shape repeats."""


def sealing(pg_sessions: LoginSessions) -> StepStorageSealedImpl:
    keys = SessionKeysImpl(PrivacyStoragePostgresImpl(pg_sessions), KeyServiceMemoryImpl())
    return StepStorageSealedImpl(StepStoragePostgresImpl(pg_sessions), keys)


class TestSealedStepStoragePostgres(StepStorageContract):
    @pytest.fixture
    def storage(self, pg_sessions: LoginSessions) -> StepStorageInterface:
        return sealing(pg_sessions)


class TestRoutedStepStoragePostgres(StepStorageContract):
    @pytest.fixture
    def storage(self, pg_sessions: LoginSessions) -> StepStorageInterface:
        history = StepStoragePostgresImpl(pg_sessions)
        privacy = PrivacyStoragePostgresImpl(pg_sessions)
        return StepStorageRoutedImpl(
            sealed=StepStorageSealedImpl(history, SessionKeysImpl(privacy, KeyServiceMemoryImpl())),
            shape_only=StepStorageShapeOnlyImpl(history),
            transient=StepStorageMemoryImpl(),
            policies=privacy,
        )


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
def infra(tmp_path: Path) -> InfraLocalImpl:
    return InfraLocalImpl(tmp_path)


@pytest.fixture
def managers(storage: StoragePostgresImpl, infra: InfraLocalImpl) -> Managers:
    return build_managers(storage, infra)


async def an_org(managers: Managers) -> TenantContext:
    slug = f"ajax-{new_id().hex[-8:]}"
    owner, _ = await managers.tenancy.bootstrap(
        RequestContext(request_id=new_id(), app=APP), "Ajax", slug, f"ann-{slug}@x.test", "Ann"
    )
    return owner


async def a_session(managers: Managers, ctx: TenantContext) -> UUID:
    return (await managers.agent_sessions.create_session(ctx, make_session())).id


async def rows(
    pg_sessions: LoginSessions, role: DatabaseRole, statement: str, org_id: UUID, **values: Any
) -> list[dict[str, Any]]:
    """What a reader of the database sees, under the tenant's own scope."""
    async with pg_sessions[role]() as session:
        await set_scope(session, org_id, None, None)
        result = await session.execute(text(statement), {"org": org_id, **values})
        return [dict(row) for row in result.mappings()]


async def history_rows(pg_sessions: LoginSessions, org_id: UUID, session_id: UUID) -> list[dict]:
    return await rows(
        pg_sessions,
        DatabaseRole.ACTIVITY,
        "SELECT id, seq, type, header::text AS header, content::text AS content,"
        " children::text AS children FROM activity.steps"
        " WHERE org_id = :org AND session_id = :session ORDER BY seq",
        org_id,
        session=session_id,
    )


async def key_rows(pg_sessions: LoginSessions, org_id: UUID) -> list[dict]:
    return await rows(
        pg_sessions,
        DatabaseRole.CORE,
        "SELECT session_id, version, wrapped, wrapping, destroyed_at FROM core.session_keys"
        " WHERE org_id = :org ORDER BY session_id, version",
        org_id,
    )


def says_something(step: Step) -> bool:
    return bool(step.content.blocks) or step.children != Children()


async def test_a_steps_content_row_holds_ciphertext_and_reads_back_plain(
    managers: Managers, pg_sessions: LoginSessions
) -> None:
    ctx = await an_org(managers)
    session = await a_session(managers, ctx)
    epoch = await managers.steps.begin_run(ctx, session)
    loop = a_loop(session)
    written = await managers.steps.append_steps(ctx, session, epoch, loop)

    raw = await history_rows(pg_sessions, ctx.org_id, session)
    said = {step.id for step in loop if says_something(step)}
    assert len(raw) == len(loop) and len(said) == 3
    for row in raw:
        content, children = json.loads(row["content"]), json.loads(row["children"])
        assert children == {"thinking": [], "attachments": []}
        if row["id"] in said:
            assert (content["state"], content["blocks"]) == ("sealed", [])
            assert content["sealed"]["version"] == 1
            assert len(content["sealed"]["ciphertext"]) > 40
        else:
            assert content == {"blocks": [], "state": "plain", "sealed": None}
    plain = json.dumps([step.model_dump(mode="json") for step in loop])
    assert all(phrase in plain for phrase in SAID), "what the loop says"
    reader = json.dumps(raw, default=str)
    assert not [phrase for phrase in SAID if phrase in reader]

    read = (await managers.steps.get_steps(ctx, session, 0, 50)).items
    assert read == written
    assert read[0].as_text() == "the weekly report is missing a total"


async def test_a_revoked_key_leaves_the_history_rows_and_none_of_their_content(
    managers: Managers, pg_sessions: LoginSessions
) -> None:
    ctx = await an_org(managers)
    session, other = await a_session(managers, ctx), await a_session(managers, ctx)
    epoch = await managers.steps.begin_run(ctx, session)
    loop = a_loop(session)
    await managers.steps.append_steps(ctx, session, epoch, loop)
    await managers.privacy.rotate_key(ctx, session)
    await managers.steps.append_inputs(ctx, session, [make_message(session, "after the rotation")])
    await managers.steps.append_inputs(ctx, other, [make_message(other, "kept")])
    before = await history_rows(pg_sessions, ctx.org_id, session)
    shape = [
        step.model_dump(exclude={"content", "children"})
        for step in (await managers.steps.get_steps(ctx, session, 0, 50)).items
    ]

    await managers.privacy.revoke_key(ctx, session)

    assert await history_rows(pg_sessions, ctx.org_id, session) == before
    keys = await key_rows(pg_sessions, ctx.org_id)
    revoked = [key for key in keys if key["session_id"] == session]
    assert [key["version"] for key in revoked] == [1, 2]
    assert all(key["wrapped"] is None and key["wrapping"] is None for key in revoked)
    assert all(key["destroyed_at"] is not None for key in revoked)
    assert [key["wrapped"] is not None for key in keys if key["session_id"] == other] == [True]

    after = (await managers.steps.get_steps(ctx, session, 0, 50)).items
    assert [step.model_dump(exclude={"content", "children"}) for step in after] == shape
    assert {step.content.state for step in after} == {ContentState.ABSENT, ContentState.PLAIN}
    assert all(step.content.blocks == () for step in after)
    assert (await managers.steps.get_steps(ctx, other, 0, 10)).items[0].as_text() == "kept"


async def test_a_rotation_rewraps_the_keys_and_rewrites_no_content_row(
    managers: Managers, pg_sessions: LoginSessions, infra: InfraLocalImpl
) -> None:
    ctx = await an_org(managers)
    sessions = [await a_session(managers, ctx) for _ in range(2)]
    for session in sessions:
        await managers.steps.append_inputs(ctx, session, [make_message(session)])
    await managers.privacy.rotate_key(ctx, sessions[0])
    await managers.steps.append_inputs(ctx, sessions[0], [make_message(sessions[0], "later")])
    history = {s: await history_rows(pg_sessions, ctx.org_id, s) for s in sessions}
    read = {s: (await managers.steps.get_steps(ctx, s, 0, 10)).items for s in sessions}
    keys = await key_rows(pg_sessions, ctx.org_id)
    assert {key["wrapping"] for key in keys} == {"memory:1"}

    rotated = utcnow()
    service = infra.get_keys()
    assert isinstance(service, KeyServiceMemoryImpl)
    service.rotate(ctx.org_id)
    assert await managers.privacy.rewrap_keys(ctx, rotated) == len(keys) == 3
    assert await managers.privacy.rewrap_keys(ctx, rotated) == 0

    rewrapped = await key_rows(pg_sessions, ctx.org_id)
    assert {key["wrapping"] for key in rewrapped} == {"memory:2"}
    assert all(old["wrapped"] != new["wrapped"] for old, new in zip(keys, rewrapped, strict=True))
    for session in sessions:
        assert await history_rows(pg_sessions, ctx.org_id, session) == history[session]
        assert (await managers.steps.get_steps(ctx, session, 0, 10)).items == read[session]


async def test_a_memory_only_session_writes_no_content_row(
    managers: Managers,
    storage: StoragePostgresImpl,
    infra: InfraLocalImpl,
    pg_sessions: LoginSessions,
) -> None:
    """With nothing at rest allowed, the history holds no row of the session
    at all, not even its cursor. With its shape kept, it holds each step's
    shape and nothing the step said, and no key is made; a process that
    does not hold the session reads every step it said as absent."""
    ctx = await an_org(managers)
    nothing = StoragePolicy(mode=StorageMode.MEMORY_ONLY, keep_shape=False)
    shape = StoragePolicy(mode=StorageMode.MEMORY_ONLY, keep_shape=True)
    sessions: dict[str, UUID] = {}
    for name, policy in (("nothing", nothing), ("shape", shape)):
        session = await a_session(managers, ctx)
        await managers.privacy.set_policy(ctx, session, policy)
        epoch = await managers.steps.begin_run(ctx, session)
        loop = a_loop(session)
        written = await managers.steps.append_steps(ctx, session, epoch, loop)
        assert (await managers.steps.get_steps(ctx, session, 0, 50)).items == written
        sessions[name] = session

    assert await history_rows(pg_sessions, ctx.org_id, sessions["nothing"]) == []
    cursors = await rows(
        pg_sessions,
        DatabaseRole.ACTIVITY,
        "SELECT session_id FROM activity.step_cursors WHERE org_id = :org",
        ctx.org_id,
    )
    assert [row["session_id"] for row in cursors] == [sessions["shape"]]

    kept = await history_rows(pg_sessions, ctx.org_id, sessions["shape"])
    assert len(kept) == 6
    for row in kept:
        content = json.loads(row["content"])
        assert content["state"] in ("absent", "plain") and content["sealed"] is None
        assert content["blocks"] == [] and json.loads(row["children"]) == {
            "thinking": [],
            "attachments": [],
        }
    reader = json.dumps(kept, default=str)
    assert not [phrase for phrase in SAID if phrase in reader]
    assert await key_rows(pg_sessions, ctx.org_id) == []

    elsewhere = build_managers(storage, infra)
    shapes = (await elsewhere.steps.get_steps(ctx, sessions["shape"], 0, 50)).items
    assert [step.seq for step in shapes] == list(range(1, 7))
    assert ContentState.ABSENT in {step.content.state for step in shapes}
    assert all(step.content.blocks == () for step in shapes)
