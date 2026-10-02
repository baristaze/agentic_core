"""An artifact over the memory roots: its text is sealed under its session's
key like a step's content, reads back plain, is noise once the key is
revoked while its record stays, is never kept for a session that keeps no
content at rest, and goes with its session's history, or its tenant's, when
the sweep purges them."""

from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from contracts.agent_session_storage import make_session
from contracts.doubles import Members, context
from contracts.histories import History

from acme.infra.buckets import Buckets
from acme.infra.impl.local import InfraLocalImpl
from acme.integrations.model_providers.registry import absent_model_providers
from acme.om.agent_sessions.impl.manager import AgentSessionsOptions
from acme.om.base import new_id
from acme.om.context import Role, TenantContext
from acme.om.exceptions import KeyRevoked, NotFound, Unavailable
from acme.om.privacy.impl.artifacts import ArtifactSealKeysImpl
from acme.om.privacy.impl.keys import SessionKeysImpl
from acme.om.privacy.types.session_privacy import StorageMode, StoragePolicy
from acme.om.root import Managers, build_managers
from acme.om.steps.types.header import ArtifactRef, ToolResponseHeader
from acme.om.steps.types.step import Step
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.om.windows import rules
from acme.om.windows.impl.gate import CallGateNullImpl
from acme.om.windows.impl.hashes import PromptHashNullImpl
from acme.om.windows.impl.manager import WindowsManagerImpl, WindowsOptions
from acme.om.windows.impl.seal import ArtifactSealNullImpl
from acme.om.windows.types.policy import CompactionPolicy

LINE = "a line of the whole log"


class Roots:
    def __init__(self, tmp_path: Path) -> None:
        self.storage = StorageMemoryImpl()
        self.infra = InfraLocalImpl(tmp_path)
        # Marked today, purged today: the retention is not what is tested.
        options = AgentSessionsOptions(retention=timedelta(0))
        self.managers: Managers = build_managers(
            self.storage, self.infra, agent_sessions_options=options
        )
        self.ctx: TenantContext = context(Role.MEMBER)

    async def session(self) -> UUID:
        created = await self.managers.agent_sessions.create_session(self.ctx, make_session())
        return created.id

    async def stored(self, session_id: UUID, artifact: ArtifactRef) -> bytes:
        key = rules.artifact_key(session_id, artifact.id)
        return await self.infra.get_buckets().get(self.ctx.org_id, Buckets.ARTIFACTS, key)

    async def present(self, session_id: UUID, artifact: ArtifactRef) -> bool:
        key = rules.artifact_key(session_id, artifact.id)
        return await self.infra.get_buckets().exists(self.ctx.org_id, Buckets.ARTIFACTS, key)


@pytest.fixture
def roots(tmp_path: Path) -> Roots:
    return Roots(tmp_path)


def a_large_result(session_id: UUID) -> tuple[Step, str]:
    history = History(session_id)
    objective = history.message("Read the whole log.")
    reply = history.response(history.request((objective,)), "", [("c1", "read_log", {})])
    text = "".join(f"{LINE} {n:07d}\n" for n in range(1_200))
    return history.result(history.call(reply, "c1"), text), text


async def kept(roots: Roots, session_id: UUID) -> tuple[ArtifactRef, str]:
    result, text = a_large_result(session_id)
    bounded = await roots.managers.windows.bound_tool_response(roots.ctx, session_id, result)
    header = bounded.header
    assert isinstance(header, ToolResponseHeader) and header.artifact is not None
    return header.artifact, text


async def read_whole(roots: Roots, session_id: UUID, artifact: ArtifactRef) -> str:
    pages, offset = [], 0
    while True:
        page = await roots.managers.windows.get_artifact(
            roots.ctx, session_id, artifact.id, offset, 100_000
        )
        pages.append(page.text)
        offset += len(page.text)
        if not page.has_more:
            return "".join(pages)


async def test_an_artifacts_text_is_sealed_at_rest_and_reads_back_plain(roots: Roots) -> None:
    session = await roots.session()
    artifact, text = await kept(roots, session)
    stored = await roots.stored(session, artifact)
    assert LINE.encode() not in stored, "the store holds no text in the clear"
    assert len(stored) > len(text.encode())
    assert await read_whole(roots, session, artifact) == text


async def test_a_revoked_key_leaves_the_record_and_its_text_noise(roots: Roots) -> None:
    session = await roots.session()
    artifact, _ = await kept(roots, session)
    before = await roots.stored(session, artifact)
    await roots.managers.privacy.revoke_key(roots.ctx, session)
    assert await roots.stored(session, artifact) == before, "nothing is rewritten"
    with pytest.raises(KeyRevoked):
        await roots.managers.windows.get_artifact(roots.ctx, session, artifact.id, 0, 10)
    record = await roots.storage.get_window_storage().read_artifact(
        roots.ctx.org_id, session, artifact.id
    )
    assert record is not None and record.characters == artifact.characters
    result, _ = a_large_result(session)
    with pytest.raises(KeyRevoked):
        await roots.managers.windows.bound_tool_response(roots.ctx, session, result)


async def test_a_sealed_artifact_opens_only_as_itself(roots: Roots) -> None:
    keys = SessionKeysImpl(roots.storage.get_privacy_storage(), roots.infra.get_keys())
    seal = ArtifactSealKeysImpl(keys, roots.storage.get_privacy_storage())
    session, artifact = await roots.session(), new_id()
    blob = await seal.seal(roots.ctx, session, artifact, b"what the tool printed")
    assert blob is not None
    assert await seal.open(roots.ctx, session, artifact, blob) == b"what the tool printed"
    with pytest.raises(ValueError, match="does not open"):
        await seal.open(roots.ctx, session, new_id(), blob)
    other = await roots.session()
    assert await seal.open(roots.ctx, other, artifact, blob) is None, "it holds no such key"
    await seal.seal(roots.ctx, other, artifact, b"another session's own")
    with pytest.raises(ValueError, match="does not open"):
        await seal.open(roots.ctx, other, artifact, blob)


async def test_a_memory_only_session_bounds_its_result_and_holds_it_in_memory_alone(
    roots: Roots,
) -> None:
    session = await roots.session()
    policy = StoragePolicy(mode=StorageMode.MEMORY_ONLY, keep_shape=False)
    await roots.managers.privacy.set_policy(roots.ctx, session, policy)
    artifact, text = await kept(roots, session)
    keys = await roots.infra.get_buckets().list(roots.ctx.org_id, Buckets.ARTIFACTS, "", 10)
    assert keys == [], "nothing it said is at rest, sealed or not"
    windows = roots.storage.get_window_storage()
    assert await windows.read_artifact(roots.ctx.org_id, session, artifact.id) is None
    assert await read_whole(roots, session, artifact) == text, "this runtime holds it whole"
    assert await roots.managers.windows.purge_artifacts(roots.ctx.org_id, session) == 0
    with pytest.raises(NotFound):
        await roots.managers.windows.get_artifact(roots.ctx, session, artifact.id, 0, 10)


async def test_an_artifact_goes_with_its_sessions_history(roots: Roots) -> None:
    session, other = await roots.session(), await roots.session()
    gone, _ = await kept(roots, session)
    stays, text = await kept(roots, other)
    await roots.managers.agent_sessions.delete_session(roots.ctx, session)
    assert await roots.managers.agent_sessions.purge_across_tenants() == 1
    assert not await roots.present(session, gone), "the object goes"
    windows = roots.storage.get_window_storage()
    assert await windows.read_artifact(roots.ctx.org_id, session, gone.id) is None
    with pytest.raises(NotFound):
        await roots.managers.windows.get_artifact(roots.ctx, session, gone.id, 0, 10)
    assert await read_whole(roots, other, stays) == text, "another session's stays"


async def test_a_purge_whose_object_went_already_still_takes_the_record(roots: Roots) -> None:
    session = await roots.session()
    artifact, _ = await kept(roots, session)
    key = rules.artifact_key(session, artifact.id)
    await roots.infra.get_buckets().delete(roots.ctx.org_id, Buckets.ARTIFACTS, key)
    assert await roots.managers.windows.purge_artifacts(roots.ctx.org_id, session) == 1
    assert await roots.managers.windows.purge_artifacts(roots.ctx.org_id, session) == 0


async def test_a_deleted_tenants_artifacts_go_a_batch_a_pass(roots: Roots) -> None:
    members = Members()  # pyright: ignore[reportAbstractUsage] (a partial double)
    managers = roots.managers
    keys = SessionKeysImpl(roots.storage.get_privacy_storage(), roots.infra.get_keys())
    windows = WindowsManagerImpl(
        roots.storage.get_window_storage(),
        managers.steps,
        members,
        managers.models,
        managers.attribution,
        absent_model_providers(),
        roots.infra.get_buckets(),
        CallGateNullImpl(),
        PromptHashNullImpl(),
        ArtifactSealKeysImpl(keys, roots.storage.get_privacy_storage()),
        CompactionPolicy(),
        WindowsOptions(purge_batch=2),
    )
    sessions = [await roots.session() for _ in range(3)]
    artifacts = [(session, (await kept(roots, session))[0]) for session in sessions]
    assert await windows.purge_tenant(roots.ctx) == 0, "a live tenant keeps everything"
    members.expired = True
    assert await windows.purge_tenant(roots.ctx) == 2, "one batch a pass"
    assert await windows.purge_tenant(roots.ctx) == 1
    assert await windows.purge_tenant(roots.ctx) == 0
    for session, artifact in artifacts:
        assert not await roots.present(session, artifact)


async def test_a_root_with_no_key_service_keeps_no_artifact(tmp_path: Path) -> None:
    roots = Roots(tmp_path)
    managers = build_managers(roots.storage, roots.infra, artifact_seal=ArtifactSealNullImpl())
    session = (await managers.agent_sessions.create_session(roots.ctx, make_session())).id
    result, _ = a_large_result(session)
    with pytest.raises(Unavailable):
        await managers.windows.bound_tool_response(roots.ctx, session, result)
    keys = await roots.infra.get_buckets().list(roots.ctx.org_id, Buckets.ARTIFACTS, "", 10)
    assert keys == []
