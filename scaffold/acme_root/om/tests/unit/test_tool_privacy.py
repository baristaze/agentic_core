"""What a tool call keeps of its session's content goes under the session's
key, over the memory roots as a process wires them. The transport's record
of a command, on the twin and on this host, keeps how it ended readable and
its output sealed: it opens under its session's key alone, and is noise
once that key is revoked. When the sweep purges the session, its
workspace's files and its records go. A tool input's hash, as the loop
writes it, is keyed by its session."""

import asyncio
import hashlib
import sys
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from contracts.agent_session_storage import make_session
from contracts.doubles import context
from contracts.loops import Loop, loop_over, reply, said, use
from contracts.tools import (
    HOST_SPEC,
    TWIN_SPEC,
    Call,
    Command,
    echoing,
    failure_of,
    put_call,
    registry_of,
    result_text,
)

from acme.infra.exceptions import InfraNotFound
from acme.infra.impl.local import InfraLocalImpl
from acme.infra.transports import RecordSeal, TransportInterface
from acme.infra.transports.local import DEFAULT_PATH, TransportLocalImpl
from acme.infra.transports.twin import TransportTwinImpl
from acme.infra.workspaces import Workspace, WorkspaceProviderInterface
from acme.infra.workspaces.host import WorkspaceHostImpl
from acme.om.agent_sessions.impl.manager import AgentSessionsOptions
from acme.om.agents.types.run import RunEnd
from acme.om.base import new_id
from acme.om.context import Role, TenantContext
from acme.om.exceptions import KeyRevoked
from acme.om.privacy.impl.keys import SessionKeysImpl
from acme.om.privacy.impl.records import RecordSealKeysImpl
from acme.om.privacy.types.session_privacy import StorageMode, StoragePolicy
from acme.om.root import Managers, build_managers
from acme.om.steps.types.header import ToolRequestHeader
from acme.om.steps.types.step import StepType
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.om.tools.rules import INPUT_HASH, canonical_input
from acme.om.tools.types.tool import Effect

LINE = "the-build-log-line"
"""What the command prints, which nothing at rest may hold in the clear."""

REGISTRY = registry_of(Command("deploy", effect=Effect.UNSAFE))


class HostInfra(InfraLocalImpl):
    """The local root with a directory on this host for each workspace, and
    the transport that runs its commands as processes here, its records
    under `records`."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.records = root / "records"
        python = Path(sys.executable).parent
        self._host = WorkspaceHostImpl(root / "workspaces")
        self._local = TransportLocalImpl(
            self.records,
            self.get_secrets(),
            self.get_broker(),
            search_path=f"{python}:{DEFAULT_PATH}",
        )

    def get_workspaces(self) -> WorkspaceProviderInterface:
        return self._host

    def get_transport(self) -> TransportInterface:
        return self._local


class Roots:
    def __init__(self, tmp_path: Path, where: str) -> None:
        self.storage = StorageMemoryImpl()
        self.infra = HostInfra(tmp_path) if where == "host" else InfraLocalImpl(tmp_path)
        self.spec = HOST_SPEC if where == "host" else TWIN_SPEC
        transport = self.infra.get_transport()
        if isinstance(transport, TransportTwinImpl):
            transport.handler = echoing()
        self.transport = transport
        # Marked today, purged today: the retention is not what is tested.
        options = AgentSessionsOptions(retention=timedelta(0))
        self.managers: Managers = build_managers(
            self.storage, self.infra, agent_sessions_options=options
        )
        self.ctx: TenantContext = context(Role.MEMBER)

    async def session(self) -> UUID:
        created = await self.managers.agent_sessions.create_session(self.ctx, make_session())
        return created.id

    async def ran(self, session_id: UUID) -> tuple[Workspace, Call]:
        """The session's workspace, and an unsafe call in its history whose
        command ran and printed `LINE`."""
        tools = self.managers.tools
        workspace = await tools.prepare_workspace(self.ctx, session_id, self.spec)
        call_input = {"argv": ["sh", "-c", f"echo {LINE}"]}
        found = await put_call(
            tools, self.managers.steps, self.ctx, "deploy", call_input, "execute", session_id
        )
        response = await tools.execute(
            self.ctx,
            REGISTRY,
            found.request,
            found.call_input,
            workspace,
            epoch=found.epoch,
            tree_deadline=None,
        )
        assert failure_of(response) is None and LINE in result_text(response)
        return workspace, found

    def at_rest(self, workspace_id: UUID) -> bytes:
        """Every record the transport keeps of the workspace, as kept."""
        if isinstance(self.transport, TransportTwinImpl):
            kept = self.transport.records.items()
            return b"".join(r.model_dump_json().encode() for (w, _), r in kept if w == workspace_id)
        assert isinstance(self.infra, HostInfra)
        folder = self.infra.records / workspace_id.hex
        return b"".join(path.read_bytes() for path in sorted(folder.glob("*.json")))

    async def holds_files(self, workspace: Workspace) -> bool:
        """Whether anything of the workspace's files is left where they were
        kept: a directory on this host, or the twin's memory."""
        if isinstance(self.infra, HostInfra):
            return await asyncio.to_thread(Path(workspace.location).exists)
        try:
            await self.transport.read_file(workspace, "notes.txt", 10)
        except InfraNotFound:
            return False
        return True

    def seal(self, session_id: UUID, key: UUID) -> RecordSeal:
        """The seal a command of the session under `key` goes with, bound as
        the tools manager binds it, over the root's own keys."""
        privacy = self.storage.get_privacy_storage()
        records = RecordSealKeysImpl(SessionKeysImpl(privacy, self.infra.get_keys()), privacy)
        return RecordSeal(
            seal=lambda data: records.seal(self.ctx, session_id, key, data),
            open=lambda sealed: records.open(self.ctx, session_id, key, sealed),
        )


@pytest.fixture(params=["twin", "host"])
def roots(tmp_path: Path, request: pytest.FixtureRequest) -> Roots:
    return Roots(tmp_path, request.param)


async def test_a_records_output_opens_under_its_sessions_key_alone_and_is_noise_once_revoked(
    roots: Roots,
) -> None:
    session = await roots.session()
    workspace, found = await roots.ran(session)
    kept = roots.at_rest(workspace.id)
    assert b'"exit_code":0' in kept, "how it ended is readable"
    assert LINE.encode() not in kept, "what it printed is sealed"

    later = await roots.managers.steps.begin_run(roots.ctx, session)
    settled = await roots.managers.tools.recover(
        roots.ctx,
        REGISTRY,
        found.request,
        found.call_input,
        workspace,
        epoch=later,
        tree_deadline=None,
    )
    assert "transport's record" in result_text(settled) and LINE in result_text(settled)
    other = roots.seal(await roots.session(), found.request.id)
    await other.seal(b"another session's own")
    for elsewhere in (other, roots.seal(session, new_id())):
        with pytest.raises(ValueError, match="does not open"):
            await roots.transport.outcome(workspace, found.request.id, later, seal=elsewhere)

    await roots.managers.privacy.revoke_key(roots.ctx, session)
    assert roots.at_rest(workspace.id) == kept, "nothing is rewritten"
    erased = await roots.transport.outcome(
        workspace, found.request.id, later, seal=roots.seal(session, found.request.id)
    )
    assert erased is not None and (erased.exit_code, erased.timed_out) == (0, False)
    assert (erased.stdout, erased.stderr) == ("", ""), "the output is noise"


async def test_a_memory_only_sessions_record_keeps_how_its_command_ended_alone(
    roots: Roots,
) -> None:
    session = await roots.session()
    policy = StoragePolicy(mode=StorageMode.MEMORY_ONLY, keep_shape=True)
    await roots.managers.privacy.set_policy(roots.ctx, session, policy)
    workspace, found = await roots.ran(session)
    kept = roots.at_rest(workspace.id)
    assert b'"exit_code":0' in kept and b'"output":null' in kept, (
        "nothing it printed, sealed or not"
    )
    later = await roots.managers.steps.begin_run(roots.ctx, session)
    recorded = await roots.transport.outcome(
        workspace, found.request.id, later, seal=roots.seal(session, found.request.id)
    )
    assert recorded is not None and (recorded.exit_code, recorded.stdout) == (0, "")


async def test_a_purged_sessions_workspace_and_records_are_gone_from_the_host(
    roots: Roots,
) -> None:
    session, other = await roots.session(), await roots.session()
    (gone, made), (stays, kept) = await roots.ran(session), await roots.ran(other)
    for workspace, call in ((gone, made), (stays, kept)):
        await roots.transport.write_file(workspace, "notes.txt", LINE.encode(), call.epoch)
        assert await roots.holds_files(workspace) and roots.at_rest(workspace.id)
    await roots.managers.agent_sessions.delete_session(roots.ctx, session)
    assert await roots.managers.agent_sessions.purge_across_tenants() == 1
    assert not await roots.holds_files(gone), "the purged session's files go"
    assert roots.at_rest(gone.id) == b"", "and its records"
    if isinstance(roots.infra, HostInfra):
        assert not (roots.infra.records / gone.id.hex).exists()
    assert await roots.holds_files(stays) and roots.at_rest(stays.id), "another session's stay"


def lock_a_module_cache(location: str) -> None:
    """A module cache left read-only in the workspace, as `go mod download`
    leaves its own under a home that is the workspace."""
    cache = Path(location) / "go" / "pkg" / "mod" / "m@v1"
    cache.mkdir(parents=True)
    (cache / "go.mod").write_text("module m\n")
    cache.chmod(0o555)


async def test_a_workspace_a_command_left_read_only_is_purged(tmp_path: Path) -> None:
    roots = Roots(tmp_path, "host")
    session = await roots.session()
    workspace, _ = await roots.ran(session)
    await asyncio.to_thread(lock_a_module_cache, workspace.location)
    await roots.managers.agent_sessions.delete_session(roots.ctx, session)
    assert await roots.managers.agent_sessions.purge_across_tenants() == 1
    assert not await roots.holds_files(workspace)
    sessions = roots.storage.get_agent_session_storage()
    assert await sessions.read_session(roots.ctx.org_id, session) is None


async def test_a_session_whose_workspace_cannot_go_fails_alone_and_stays_claimed(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    stuck, other = await roots.session(), await roots.session()
    (held, made), (gone, kept) = await roots.ran(stuck), await roots.ran(other)
    for workspace, call in ((held, made), (gone, kept)):
        await roots.transport.write_file(workspace, "notes.txt", LINE.encode(), call.epoch)
    provider = roots.infra.get_workspaces()
    purge = provider.purge

    async def refusing(org_id: UUID, workspace_id: UUID) -> None:
        if workspace_id == stuck:
            raise PermissionError("a file there cannot be removed")
        await purge(org_id, workspace_id)

    monkeypatch.setattr(provider, "purge", refusing)
    for session in (stuck, other):
        await roots.managers.agent_sessions.delete_session(roots.ctx, session)
    assert await roots.managers.agent_sessions.purge_across_tenants() == 2
    sessions = roots.storage.get_agent_session_storage()
    assert not await roots.holds_files(gone), "the other session is purged"
    assert await sessions.read_session(roots.ctx.org_id, other) is None
    left = await sessions.read_session(roots.ctx.org_id, stuck)
    assert left is not None and left.purge_started_at is not None, "it stays claimed"
    assert await roots.holds_files(held)

    monkeypatch.setattr(provider, "purge", purge)
    assert await roots.managers.agent_sessions.purge_across_tenants() == 1, "the next pass"
    assert not await roots.holds_files(held)
    assert await sessions.read_session(roots.ctx.org_id, stuck) is None


async def calls_lookup(loop: Loop) -> tuple[UUID, str]:
    """A session whose loop calls `lookup` with the one input every such
    session gives it, and the hash its request recorded."""
    session_id = await loop.start()
    await loop.say(session_id, "What is the total?")
    loop.anthropic.add(reply(said("Looking."), use("lookup")), reply(said("It is 12.")))
    run = await loop.loops.run(loop.owner, session_id)
    assert run.end is RunEnd.ENDED
    steps = await loop.history(session_id)
    (request,) = [step for step in steps if step.type is StepType.TOOL_REQUEST]
    assert isinstance(request.header, ToolRequestHeader)
    return session_id, request.header.input_hash


async def test_a_tool_inputs_hash_is_keyed_by_its_session(tmp_path: Path) -> None:
    loop = loop_over(tmp_path)
    first, first_hash = await calls_lookup(loop)
    second, second_hash = await calls_lookup(loop)
    same = {"q": "the total"}
    plain = hashlib.sha256(canonical_input(same)).hexdigest()
    assert first_hash.startswith(INPUT_HASH) and plain not in first_hash
    assert first_hash != second_hash, "two sessions' equal inputs hash apart"
    tools = loop.managers.tools
    assert await tools.input_hash(loop.owner, first, same) == first_hash, "alike within one"
    assert await tools.input_hash(loop.owner, second, same) == second_hash
    await loop.managers.privacy.revoke_key(loop.owner, first)
    with pytest.raises(KeyRevoked):
        await tools.input_hash(loop.owner, first, same)
