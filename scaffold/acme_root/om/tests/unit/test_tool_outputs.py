"""A tool output over the memory roots: its text is sealed under its session's
key like a step's content, reads back plain, is noise once the key is
revoked while its record stays, is never kept for a session that keeps no
content at rest, and goes with its session's history, or its tenant's, when
the sweep purges them."""

from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from contracts.agent_session_storage import make_agent_session
from contracts.doubles import Members, context
from contracts.histories import History

from acme.infra.buckets import Buckets
from acme.infra.impl.local import InfraLocalImpl
from acme.integrations.model_providers.registry import absent_model_providers
from acme.om.agent_sessions.impl.manager import AgentSessionsOptions
from acme.om.agent_steps.impl.manager import no_session
from acme.om.agent_steps.types.agent_step import AgentStep
from acme.om.agent_steps.types.header import ToolOutputRef, ToolResponseHeader
from acme.om.base import new_id
from acme.om.context import Role, TenantContext
from acme.om.exceptions import KeyRevoked, NotFound, Unavailable
from acme.om.models.impl.credentials import CallCredentialsPlatformImpl
from acme.om.privacy.impl.keys import AgentSessionKeysImpl
from acme.om.privacy.impl.tool_outputs import ToolOutputSealKeysImpl
from acme.om.privacy.types.agent_session_privacy import StorageMode, StoragePolicy
from acme.om.root import Managers, build_managers
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.om.windows import rules
from acme.om.windows.impl.gate import CallGateNullImpl
from acme.om.windows.impl.hashes import PromptHashNullImpl
from acme.om.windows.impl.manager import WindowsManagerImpl, WindowsOptions
from acme.om.windows.impl.seal import ToolOutputSealNullImpl
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
        created = await self.managers.agent_sessions.create_agent_session(
            self.ctx, make_agent_session()
        )
        return created.id

    async def stored(self, agent_session_id: UUID, tool_output: ToolOutputRef) -> bytes:
        key = rules.tool_output_key(agent_session_id, tool_output.id)
        return await self.infra.get_buckets().get(self.ctx.org_id, Buckets.TOOL_OUTPUTS, key)

    async def present(self, agent_session_id: UUID, tool_output: ToolOutputRef) -> bool:
        key = rules.tool_output_key(agent_session_id, tool_output.id)
        return await self.infra.get_buckets().exists(self.ctx.org_id, Buckets.TOOL_OUTPUTS, key)


@pytest.fixture
def roots(tmp_path: Path) -> Roots:
    return Roots(tmp_path)


def a_large_result(agent_session_id: UUID) -> tuple[AgentStep, str]:
    history = History(agent_session_id)
    objective = history.message("Read the whole log.")
    reply = history.response(history.request((objective,)), "", [("c1", "read_log", {})])
    text = "".join(f"{LINE} {n:07d}\n" for n in range(1_200))
    return history.result(history.call(reply, "c1"), text), text


async def kept(roots: Roots, agent_session_id: UUID) -> tuple[ToolOutputRef, str]:
    result, text = a_large_result(agent_session_id)
    bounded = await roots.managers.windows.bound_tool_response(roots.ctx, agent_session_id, result)
    header = bounded.header
    assert isinstance(header, ToolResponseHeader) and header.tool_output is not None
    return header.tool_output, text


async def read_whole(roots: Roots, agent_session_id: UUID, tool_output: ToolOutputRef) -> str:
    pages, offset = [], 0
    while True:
        page = await roots.managers.windows.get_tool_output(
            roots.ctx, agent_session_id, tool_output.id, offset, 100_000
        )
        pages.append(page.text)
        offset += len(page.text)
        if not page.has_more:
            return "".join(pages)


async def test_an_tool_outputs_text_is_sealed_at_rest_and_reads_back_plain(roots: Roots) -> None:
    session = await roots.session()
    tool_output, text = await kept(roots, session)
    stored = await roots.stored(session, tool_output)
    assert LINE.encode() not in stored, "the store holds no text in the clear"
    assert len(stored) > len(text.encode())
    assert await read_whole(roots, session, tool_output) == text


async def test_a_revoked_key_leaves_the_record_and_its_text_noise(roots: Roots) -> None:
    session = await roots.session()
    tool_output, _ = await kept(roots, session)
    before = await roots.stored(session, tool_output)
    await roots.managers.privacy.revoke_key(roots.ctx, session)
    assert await roots.stored(session, tool_output) == before, "nothing is rewritten"
    with pytest.raises(KeyRevoked):
        await roots.managers.windows.get_tool_output(roots.ctx, session, tool_output.id, 0, 10)
    record = await roots.storage.get_window_storage().read_tool_output(
        roots.ctx.org_id, session, tool_output.id
    )
    assert record is not None and record.characters == tool_output.characters
    result, _ = a_large_result(session)
    with pytest.raises(KeyRevoked):
        await roots.managers.windows.bound_tool_response(roots.ctx, session, result)


async def test_a_sealed_tool_output_opens_only_as_itself(roots: Roots) -> None:
    keys = AgentSessionKeysImpl(roots.storage.get_privacy_storage(), roots.infra.get_keys())
    seal = ToolOutputSealKeysImpl(keys, roots.storage.get_privacy_storage())
    session, tool_output = await roots.session(), new_id()
    sealed = await seal.seal(roots.ctx, session, tool_output, b"what the tool printed")
    assert sealed.at_rest and b"what the tool printed" not in sealed.blob
    blob = sealed.blob
    assert await seal.open(roots.ctx, session, tool_output, blob) == b"what the tool printed"
    with pytest.raises(ValueError, match="does not open"):
        await seal.open(roots.ctx, session, new_id(), blob)
    other = await roots.session()
    assert await seal.open(roots.ctx, other, tool_output, blob) is None, "it holds no such key"
    await seal.seal(roots.ctx, other, tool_output, b"another session's own")
    with pytest.raises(ValueError, match="does not open"):
        await seal.open(roots.ctx, other, tool_output, blob)


async def test_a_memory_only_session_bounds_its_result_and_holds_it_in_memory_alone(
    roots: Roots,
) -> None:
    session = await roots.session()
    policy = StoragePolicy(mode=StorageMode.MEMORY_ONLY, keep_shape=False)
    await roots.managers.privacy.set_policy(roots.ctx, session, policy)
    tool_output, text = await kept(roots, session)
    keys = await roots.infra.get_buckets().list(roots.ctx.org_id, Buckets.TOOL_OUTPUTS, "", 10)
    assert keys == [], "nothing it said is at rest, sealed or not"
    windows = roots.storage.get_window_storage()
    assert await windows.read_tool_output(roots.ctx.org_id, session, tool_output.id) is None
    assert await read_whole(roots, session, tool_output) == text, "this runtime holds it whole"
    assert await roots.managers.windows.purge_tool_outputs(roots.ctx.org_id, session) == 0
    with pytest.raises(NotFound):
        await roots.managers.windows.get_tool_output(roots.ctx, session, tool_output.id, 0, 10)


async def test_a_memory_only_tool_output_is_erased_with_its_sessions_key(roots: Roots) -> None:
    session = await roots.session()
    policy = StoragePolicy(mode=StorageMode.MEMORY_ONLY, keep_shape=False)
    await roots.managers.privacy.set_policy(roots.ctx, session, policy)
    tool_output, text = await kept(roots, session)
    assert await read_whole(roots, session, tool_output) == text
    await roots.managers.privacy.revoke_key(roots.ctx, session)
    for _ in range(2):
        with pytest.raises(KeyRevoked):
            await roots.managers.windows.get_tool_output(roots.ctx, session, tool_output.id, 0, 10)


async def test_an_tool_output_goes_with_its_sessions_history(roots: Roots) -> None:
    session, other = await roots.session(), await roots.session()
    gone, _ = await kept(roots, session)
    stays, text = await kept(roots, other)
    await roots.managers.agent_sessions.delete_agent_session(roots.ctx, session)
    assert await roots.managers.agent_sessions.purge_across_tenants() == 1
    assert not await roots.present(session, gone), "the object goes"
    windows = roots.storage.get_window_storage()
    assert await windows.read_tool_output(roots.ctx.org_id, session, gone.id) is None
    with pytest.raises(NotFound):
        await roots.managers.windows.get_tool_output(roots.ctx, session, gone.id, 0, 10)
    assert await read_whole(roots, other, stays) == text, "another session's stays"


async def test_a_purge_whose_object_went_already_still_takes_the_record(roots: Roots) -> None:
    session = await roots.session()
    tool_output, _ = await kept(roots, session)
    key = rules.tool_output_key(session, tool_output.id)
    await roots.infra.get_buckets().delete(roots.ctx.org_id, Buckets.TOOL_OUTPUTS, key)
    assert await roots.managers.windows.purge_tool_outputs(roots.ctx.org_id, session) == 1
    assert await roots.managers.windows.purge_tool_outputs(roots.ctx.org_id, session) == 0


async def test_a_deleted_tenants_tool_outputs_go_a_batch_a_pass(roots: Roots) -> None:
    members = Members()  # pyright: ignore[reportAbstractUsage] (a partial double)
    managers = roots.managers
    keys = AgentSessionKeysImpl(roots.storage.get_privacy_storage(), roots.infra.get_keys())
    windows = WindowsManagerImpl(
        roots.storage.get_window_storage(),
        managers.agent_steps,
        members,
        managers.models,
        managers.attribution,
        CallCredentialsPlatformImpl(absent_model_providers()),
        roots.infra.get_buckets(),
        CallGateNullImpl(),
        PromptHashNullImpl(),
        ToolOutputSealKeysImpl(keys, roots.storage.get_privacy_storage()),
        CompactionPolicy(),
        WindowsOptions(purge_batch=2),
        opens=no_session,
    )
    sessions = [await roots.session() for _ in range(3)]
    tool_outputs = [(session, (await kept(roots, session))[0]) for session in sessions]
    assert await windows.purge_tenant(roots.ctx) == 0, "a live tenant keeps everything"
    members.expired = True
    assert await windows.purge_tenant(roots.ctx) == 2, "one batch a pass"
    assert await windows.purge_tenant(roots.ctx) == 1
    assert await windows.purge_tenant(roots.ctx) == 0
    for session, tool_output in tool_outputs:
        assert not await roots.present(session, tool_output)


async def test_a_root_with_no_key_service_keeps_no_tool_output(tmp_path: Path) -> None:
    roots = Roots(tmp_path)
    managers = build_managers(roots.storage, roots.infra, tool_output_seal=ToolOutputSealNullImpl())
    session = (
        await managers.agent_sessions.create_agent_session(roots.ctx, make_agent_session())
    ).id
    result, _ = a_large_result(session)
    with pytest.raises(Unavailable):
        await managers.windows.bound_tool_response(roots.ctx, session, result)
    keys = await roots.infra.get_buckets().list(roots.ctx.org_id, Buckets.TOOL_OUTPUTS, "", 10)
    assert keys == []
