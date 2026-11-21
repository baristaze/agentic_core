"""A VM workspace's snapshots and bases, through the tools and the loop, over
the VM provider on the VMs' twin (ADR 1029, ADR 1045). A snapshot keeps the
workspace's directory alone: packed in the VM, read out in parts, each part
sealed under the session's key and kept under the session's names, and the
manifest that names them kept as any snapshot is. The next run's VM starts
on the base and takes the directory back, each part held to its hash, and
the model is told the rest is the base's again. A directory past the bound
is refused before it is packed; an archive that fails part way keeps
nothing, and a restore that fails parks the loop and touches nothing kept,
the VM it would replace included, so the person's unlock goes on from that
VM's files. A
session that keeps no content at rest keeps no archive, and a revoked key
leaves its parts noise. A fork's parts are the child's own. A base's disk
is kept under the tenant, outlives the build that made it, and goes with
the tenant's purge."""

import json
import os
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from acme.infra.agent_workspaces import (
    AgentWorkspace,
    AgentWorkspaceBase,
    AgentWorkspaceLost,
    AgentWorkspaceProviderInterface,
    DirectoryPack,
    Durability,
    EgressMode,
    EgressPolicy,
    IsolationMode,
    IsolationSpec,
    SnapshotRefused,
    packs,
)
from acme.infra.agent_workspaces.vm import WORKSPACE, AgentWorkspaceVmImpl, vm_name, vm_names
from acme.infra.buckets import Buckets, BucketsInterface
from acme.infra.buckets.local import BucketsLocalImpl
from acme.infra.exceptions import BackendFailed
from acme.infra.impl.local import InfraLocalImpl
from acme.infra.secrets.local import SecretsLocalImpl
from acme.infra.transports.broker import BrokerTwinImpl
from acme.infra.transports.vm import TransportVmImpl
from acme.infra.vms.twin import VmsTwinImpl
from acme.om.agent_root import AgentRootOptions
from acme.om.agent_steps.rules import control_step
from acme.om.agent_steps.types.agent_step import AgentStep, AgentStepType
from acme.om.agent_steps.types.content import TextBlock
from acme.om.agent_steps.types.header import (
    HASH_SCHEME,
    AgentWorkspaceSnapshot,
    ControlCommand,
    LoopOutcome,
    ParkReason,
    SnapshotHeader,
    SnapshotHolds,
)
from acme.om.agents.impl.loop import LOST_SNAPSHOT
from acme.om.agents.loop_rules import FROM_BASE, LOST
from acme.om.base import OmBase, new_id, utcnow
from acme.om.context import Role, TenantContext
from acme.om.privacy.impl.keys import AgentSessionKeysImpl
from acme.om.privacy.impl.snapshots import SnapshotSealKeysImpl
from acme.om.privacy.types.agent_session_privacy import StorageMode, StoragePolicy
from acme.om.root import build_managers
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.om.tools.impl.bases import base_agent_workspace_id, base_key
from acme.om.tools.impl.manager import ToolsOptions
from acme.om.tools.impl.snapshots import BUCKET, part_key, session_prefix
from acme.om.tools.tool import TakenSnapshot, ToolRuntime
from acme.om.tools.types.tool import Effect, ToolClass, ToolInput

from contracts.agent_session_storage import make_agent_session
from contracts.doubles import context
from contracts.factories import make_org
from contracts.loops import ASSISTANT, Asked, Found, Lookup, Loop, call, loop_over, reply, said
from contracts.tools import Tools, stand_ins, tools_over

PREFIX = "acme-test-"
PART = 1024
"""A part as small as a case's files, so an archive is many parts."""
KEPT = IsolationSpec(
    mode=IsolationMode.VM,
    egress=EgressPolicy(mode=EgressMode.OPEN),
    durability=Durability.SNAPSHOT,
)
ON_BASE = IsolationSpec(
    mode=IsolationMode.VM,
    egress=EgressPolicy(mode=EgressMode.OPEN),
    base=AgentWorkspaceBase(image="twin-image", setup=("echo from-the-base > base.txt",)),
)
PLAIN = "the plain words of the notes, " * 200
"""What a command writes into the directory: longer than a part, so it spans
several, and plain, so a part that held it in the clear would show it."""


class CountingVm(AgentWorkspaceVmImpl):
    """The VM provider, keeping each pack it made and each read of one."""

    def __init__(self, vm_twin: VmsTwinImpl) -> None:
        super().__init__(vm_twin, "twin-image", PREFIX, timedelta(seconds=60))
        self.packs: list[DirectoryPack] = []
        self.reads = 0

    async def pack(self, workspace: AgentWorkspace, bound: int) -> DirectoryPack:
        pack = await super().pack(workspace, bound)
        self.packs.append(pack)
        return pack

    async def read(self, pack: DirectoryPack, part_bytes: int) -> AsyncGenerator[bytes]:
        self.reads += 1
        async for part in super().read(pack, part_bytes):
            yield part


class FlakyBuckets(BucketsLocalImpl):
    """The local buckets, whose puts fail once `fail_after` more have held."""

    fail_after: int | None = None

    async def put(
        self,
        org_id: UUID,
        bucket: Buckets,
        key: str,
        data: bytes,
        content_type: str,
        *,
        deadline: datetime | None = None,
    ) -> None:
        if self.fail_after is not None:
            if self.fail_after == 0:
                raise BackendFailed("buckets", "put", "the store went away")
            self.fail_after -= 1
        await super().put(org_id, bucket, key, data, content_type, deadline=deadline)


class VmInfra(InfraLocalImpl):
    """The local infra root, its workspaces VMs on its VMs' twin, and its
    buckets ones a case can make fail."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        vm_twin = self.get_vms()
        assert isinstance(vm_twin, VmsTwinImpl)
        self.vm_twin = vm_twin
        self.vms = CountingVm(vm_twin)
        self.buckets = FlakyBuckets(root / "flaky-buckets")

    def get_agent_workspaces(self) -> AgentWorkspaceProviderInterface:
        return self.vms

    def get_buckets(self) -> BucketsInterface:
        return self.buckets

    def directory(self, agent_workspace_id: UUID) -> Path:
        """The workspace's directory in its VM, on the twin."""
        return directory_of(self.vm_twin, agent_workspace_id)

    async def stored(self, org_id: UUID, agent_session_id: UUID) -> dict[str, bytes]:
        """Every blob the session's snapshots keep, by key."""
        keys = await self.buckets.list(org_id, BUCKET, session_prefix(agent_session_id), 1000)
        return {key: await self.buckets.get(org_id, BUCKET, key) for key in keys}


def standing(vm_twin: VmsTwinImpl, agent_workspace_id: UUID) -> list[str]:
    """The VMs that stand under the workspace's names."""
    return [name for name in vm_names(PREFIX, agent_workspace_id) if name in vm_twin.vms]


def directory_of(vm_twin: VmsTwinImpl, agent_workspace_id: UUID) -> Path:
    """The directory of the one VM the workspace stands on."""
    (name,) = standing(vm_twin, agent_workspace_id)
    return vm_twin.vms[name].folder / WORKSPACE


def no_pack_left(packs: list[DirectoryPack]) -> bool:
    """Whether every pack is gone from its VM; on the twin a VM's temporary
    space is this host's."""
    return not any(os.path.exists(pack.path) for pack in packs)


def named(step: AgentStep) -> AgentWorkspaceSnapshot:
    assert isinstance(step.header, SnapshotHeader)
    return step.header.snapshot


def snapshots(history: list[AgentStep]) -> list[AgentWorkspaceSnapshot]:
    return [named(step) for step in history if step.type is AgentStepType.SNAPSHOTTED]


# Over the loop.


class Notes(Lookup):
    """A command's writes in the VM: its input into the directory, and a
    line outside it, as an install would leave one."""

    def __init__(self, infra: VmInfra) -> None:
        super().__init__("notes", effect=Effect.IDEMPOTENT, authorization_class=ToolClass.WRITE)
        self._infra = infra

    async def run(self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime) -> OmBase:
        assert isinstance(call_input, Asked)
        directory = self._infra.directory(runtime.agent_session_id)
        (directory / "notes.txt").write_text(call_input.q)
        (directory.parent / "installed.txt").write_text("outside the directory")
        return Found(text="written")


KEEPER = ASSISTANT.model_copy(
    update={"name": "vm_keeper", "isolation": KEPT, "tools": (*ASSISTANT.tools, "notes")}
)


def vm_loop(tmp_path: Path) -> tuple[Loop, VmInfra]:
    infra = VmInfra(tmp_path)
    loop = loop_over(
        tmp_path,
        kinds=(ASSISTANT, KEEPER),
        infra=infra,
        extra=(Notes(infra),),
        tools_options=ToolsOptions(snapshot_part_bytes=PART),
    )
    return loop, infra


async def noted(loop: Loop, agent_session_id: UUID, text: str) -> AgentWorkspaceSnapshot | None:
    """A run that writes `text` into the directory and ends: the snapshot
    its end names, or None when it kept none."""
    before = snapshots(await loop.history(agent_session_id))
    await loop.say(agent_session_id, "Write it.")
    loop.anthropic.add(reply(call("notes", q=text)), reply(said("Written.")))
    assert (await loop.loops.run(loop.owner, agent_session_id)).outcome is LoopOutcome.SUCCEEDED
    after = snapshots(await loop.history(agent_session_id))
    return after[-1] if len(after) > len(before) else None


async def test_a_run_keeps_its_vm_directory_in_sealed_parts_and_the_next_run_holds_the_same_files(
    tmp_path: Path,
) -> None:
    """The run ends with a `snapshotted` step that names a directory's
    archive. Its parts sit under the session's names, each sealed, so none
    holds the plain text. The next run's VM is a new one on the base: its
    directory holds the same files, what was written outside it is gone,
    and the model reads that it is."""
    loop, infra = vm_loop(tmp_path)
    org_id = loop.owner.org_id
    agent_session_id = await loop.start(KEEPER.name)

    snapshot = await noted(loop, agent_session_id, PLAIN)

    assert snapshot is not None and snapshot.holds is SnapshotHolds.DIRECTORY
    stored = await infra.stored(org_id, agent_session_id)
    parts = sorted(key for key in stored if "/workspace/" in key)
    assert len(parts) == -(-snapshot.size // PART) > 2, "the archive, in parts"
    assert parts == [part_key(agent_session_id, snapshot.id, index) for index in range(len(parts))]
    assert all(b"plain words" not in blob for blob in stored.values()), "sealed, never in the clear"
    assert no_pack_left(infra.vms.packs)

    await loop.say(agent_session_id, "Read it back.")
    loop.anthropic.add(reply(said("It is there.")))
    assert (await loop.loops.run(loop.owner, agent_session_id)).outcome is LoopOutcome.SUCCEEDED

    directory = infra.directory(agent_session_id)
    assert (directory / "notes.txt").read_text() == PLAIN
    assert not (directory.parent / "installed.txt").exists(), "the base's again"
    history = await loop.history(agent_session_id)
    (notice,) = [step for step in history if step.type is AgentStepType.ENVIRONMENT_CHANGED]
    assert notice.content.blocks == (TextBlock(text=FROM_BASE),)
    (kept,) = [step for step in history if step.type is AgentStepType.SNAPSHOTTED][:1]
    assert notice.refs == (kept.id,)


async def test_an_archive_that_fails_part_way_keeps_nothing_and_the_snapshot_before_stays_latest(
    tmp_path: Path,
) -> None:
    """The store fails on a later part of the second run's archive: every
    part it had put is removed, no step names it, and the next run starts
    from the workspace as it stands, never from the older archive."""
    loop, infra = vm_loop(tmp_path)
    org_id = loop.owner.org_id
    agent_session_id = await loop.start(KEEPER.name)
    first = await noted(loop, agent_session_id, PLAIN)
    assert first is not None
    kept = await infra.stored(org_id, agent_session_id)

    infra.buckets.fail_after = 2
    assert await noted(loop, agent_session_id, "written since " + PLAIN) is None
    infra.buckets.fail_after = None

    assert await infra.stored(org_id, agent_session_id) == kept, "nothing of it stays"
    assert snapshots(await loop.history(agent_session_id))[-1] == first
    assert no_pack_left(infra.vms.packs)
    await loop.say(agent_session_id, "Look again.")
    loop.anthropic.add(reply(said("Seen.")))
    assert (await loop.loops.run(loop.owner, agent_session_id)).outcome is LoopOutcome.SUCCEEDED
    notes = (infra.directory(agent_session_id) / "notes.txt").read_text()
    assert notes == "written since " + PLAIN


async def test_a_restore_that_fails_parks_the_loop_and_touches_nothing_kept(
    tmp_path: Path,
) -> None:
    """A part altered in the store loses the workspace as it is read: the
    loop parks for a person, no half-restored VM stands, and every blob
    the archive is kept in is as it was."""
    loop, infra = vm_loop(tmp_path)
    org_id = loop.owner.org_id
    agent_session_id = await loop.start(KEEPER.name)
    snapshot = await noted(loop, agent_session_id, PLAIN)
    assert snapshot is not None
    before = standing(infra.vm_twin, agent_session_id)
    altered = part_key(agent_session_id, snapshot.id, 2)
    blob = await infra.buckets.get(org_id, BUCKET, altered)
    await infra.buckets.put(org_id, BUCKET, altered, blob[:-1] + bytes([blob[-1] ^ 1]), "x")
    kept = await infra.stored(org_id, agent_session_id)

    await loop.say(agent_session_id, "Read it back.")
    parked = await loop.loops.run(loop.owner, agent_session_id)

    assert parked.park is not None and parked.park.reason is ParkReason.PERSON
    assert parked.park.cause == LOST_SNAPSHOT
    assert await infra.stored(org_id, agent_session_id) == kept, "the archive is untouched"
    assert standing(infra.vm_twin, agent_session_id) == before, "no half-restored VM"
    assert snapshots(await loop.history(agent_session_id))[-1] == snapshot


@pytest.mark.parametrize("damage", ["gone", "altered"])
async def test_a_restore_whose_part_fails_keeps_the_vm_it_would_replace_for_the_unlock(
    tmp_path: Path, damage: str
) -> None:
    """A part gone from the store, or altered in it, fails the restore once
    its new VM stands: that VM alone is destroyed, the VM it would replace
    stands as it was, and the loop parks for a person. The person's unlock
    goes on from that VM: the directory holds the files the archive held,
    and the model reads that the restore was lost."""
    loop, infra = vm_loop(tmp_path)
    org_id = loop.owner.org_id
    agent_session_id = await loop.start(KEEPER.name)
    snapshot = await noted(loop, agent_session_id, PLAIN)
    assert snapshot is not None
    (replaced,) = standing(infra.vm_twin, agent_session_id)
    failing = part_key(agent_session_id, snapshot.id, 2)
    if damage == "gone":
        await infra.buckets.delete(org_id, BUCKET, failing)
    else:
        blob = await infra.buckets.get(org_id, BUCKET, failing)
        await infra.buckets.put(org_id, BUCKET, failing, blob[:-1] + bytes([blob[-1] ^ 1]), "x")
    launched = len(infra.vm_twin.calls)

    await loop.say(agent_session_id, "Read it back.")
    parked = await loop.loops.run(loop.owner, agent_session_id)

    assert parked.park is not None and parked.park.reason is ParkReason.PERSON
    assert parked.park.cause == LOST_SNAPSHOT
    calls = infra.vm_twin.calls[launched:]
    (new,) = [call[1] for call in calls if call[0] == "launch"]
    assert new != replaced and ("destroy", new) in calls, "the new VM, destroyed"
    assert ("destroy", replaced) not in calls
    assert standing(infra.vm_twin, agent_session_id) == [replaced], "the VM it would replace"

    unlock = control_step(new_id(), utcnow(), agent_session_id, loop.owner, ControlCommand.UNLOCK)
    await loop.managers.agent_sessions.receive(loop.owner, agent_session_id, [unlock])
    loop.anthropic.add(reply(said("Going on as it stands.")))
    run = await loop.loops.run(loop.owner, agent_session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED
    assert standing(infra.vm_twin, agent_session_id) == [replaced]
    assert (infra.directory(agent_session_id) / "notes.txt").read_text() == PLAIN
    history = await loop.history(agent_session_id)
    assert [step.as_text() for step in history if step.type is AgentStepType.ENVIRONMENT_CHANGED][
        -1
    ] == LOST


# Through the tools.


class Vms:
    """The tools over the VM provider on the VMs' twin, with one
    injected secret the catalog may give a command."""

    def __init__(self, tmp_path: Path, options: ToolsOptions | None = None) -> None:
        self.vm_twin = VmsTwinImpl(tmp_path / "vms")
        self.provider = CountingVm(self.vm_twin)
        self.secrets = SecretsLocalImpl(tmp_path / "secrets.env")
        self.buckets = BucketsLocalImpl(tmp_path / "buckets")
        transport = TransportVmImpl(
            tmp_path / "records",
            self.secrets,
            BrokerTwinImpl(),
            self.vm_twin,
            timedelta(seconds=60),
        )
        self.tools: Tools = tools_over(
            transport,
            self.provider,
            options or ToolsOptions(snapshot_part_bytes=PART),
            buckets=self.buckets,
            secrets=self.secrets,
            secret_names=frozenset({"SERVICE_TOKEN"}),
        )
        self.ctx = context(Role.SERVICE, make_org())

    def directory(self, agent_workspace_id: UUID) -> Path:
        return directory_of(self.vm_twin, agent_workspace_id)

    async def snapshot(self, workspace: AgentWorkspace) -> AgentWorkspaceSnapshot:
        epoch = await self.tools.steps.begin_run(self.ctx, workspace.id)
        step = await self.tools.manager.snapshot_agent_workspace(
            self.ctx, workspace.id, workspace, epoch=epoch, loop_id=new_id(), tools=()
        )
        return named(step)

    async def stored(self, agent_session_id: UUID) -> list[str]:
        prefix = session_prefix(agent_session_id)
        return await self.buckets.list(self.ctx.org_id, BUCKET, prefix, 1000)


async def test_a_directory_past_the_bound_is_refused_before_it_is_packed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its files are measured in the VM first: the refusal names their size
    and the bound, and no pack is written, read, or kept."""
    guest_tmp = tmp_path / "guest-tmp"
    guest_tmp.mkdir()
    monkeypatch.setattr(packs, "TEMPORARY", str(guest_tmp))
    vms = Vms(tmp_path, ToolsOptions(snapshot_part_bytes=PART, directory_bound=8 * PART))
    agent_session_id = new_id()
    workspace = await vms.tools.manager.prepare_agent_workspace(vms.ctx, agent_session_id, KEPT)
    (vms.directory(agent_session_id) / "large.bin").write_bytes(os.urandom(16 * PART))

    refused = f"holds {16 * PART} bytes, past the bound of {8 * PART}"
    with pytest.raises(SnapshotRefused, match=refused):
        await vms.snapshot(workspace)

    assert vms.provider.packs == [] and list(guest_tmp.iterdir()) == [], "never packed"
    assert vms.provider.reads == 0, "not a byte of it was read"
    assert await vms.stored(agent_session_id) == []


async def test_a_secret_value_in_the_directory_refuses_the_snapshot_and_keeps_nothing(
    tmp_path: Path,
) -> None:
    vms = Vms(tmp_path)
    await vms.secrets.put(vms.ctx.org_id, "SERVICE_TOKEN", "tok-0123456789abcdef")
    agent_session_id = new_id()
    workspace = await vms.tools.manager.prepare_agent_workspace(vms.ctx, agent_session_id, KEPT)
    (vms.directory(agent_session_id) / "env").write_text("export T=tok-0123456789abcdef\n")

    with pytest.raises(SnapshotRefused, match="SERVICE_TOKEN"):
        await vms.snapshot(workspace)

    assert await vms.stored(agent_session_id) == []
    assert no_pack_left(vms.provider.packs)


async def test_a_fork_of_a_vm_keeps_the_childs_own_parts(tmp_path: Path) -> None:
    """The child's copy is parts under its own names, sealed under its own
    key, so its parent's purge leaves them whole, and the child starts on
    the parent's files. The pack goes once the child's parts are kept."""
    vms = Vms(tmp_path)
    for durability in (Durability.SNAPSHOT, Durability.CACHE):
        parent, child = new_id(), new_id()
        spec = KEPT.model_copy(update={"durability": durability})
        workspace = await vms.tools.manager.prepare_agent_workspace(vms.ctx, parent, spec)
        (vms.directory(parent) / "state.txt").write_text("the parent's")
        kept = durability is Durability.SNAPSHOT
        taken = TakenSnapshot(
            agent_workspace_id=parent,
            archive=await vms.provider.pack(workspace, 1 << 30),
            kept=kept,
        )

        copy = await vms.tools.manager.fork_snapshot(vms.ctx, child, new_id(), taken)

        assert copy.holds is SnapshotHolds.DIRECTORY and copy.agent_workspace_id == parent
        assert any("/workspace/" in key for key in await vms.stored(child))
        assert no_pack_left(vms.provider.packs)
        await vms.tools.manager.purge_agent_workspace(vms.ctx.org_id, parent)
        forked = await vms.tools.manager.prepare_agent_workspace(vms.ctx, child, KEPT, restore=copy)
        assert (vms.directory(forked.id) / "state.txt").read_text() == "the parent's"


async def test_a_vm_base_outlives_its_build_and_goes_with_the_tenants_purge(
    tmp_path: Path,
) -> None:
    vms = Vms(tmp_path)
    kept_under = vm_name(PREFIX, base_agent_workspace_id(vms.ctx.org_id, base_key(ON_BASE)))

    agent_session_id = new_id()
    await vms.tools.manager.prepare_agent_workspace(vms.ctx, agent_session_id, ON_BASE)

    built = (vms.directory(agent_session_id) / "base.txt").read_text()
    assert built == "from-the-base\n"
    (base,) = [name for name in vms.vm_twin.vms if name.startswith(kept_under)]
    assert set(vms.vm_twin.vms) == {base, vm_name(PREFIX, agent_session_id)}, (
        "the build's own VM went"
    )
    vms.tools.members.expired = True
    assert await vms.tools.manager.purge_tenant(vms.ctx) == 1
    assert await vms.vm_twin.names(kept_under) == []


# Privacy.


async def test_a_session_with_no_content_at_rest_keeps_no_archive_and_a_revoked_key_leaves_noise(
    tmp_path: Path,
) -> None:
    """A session that keeps no content at rest is refused its snapshot before
    its directory is packed, and nothing is stored. Once a kept session's
    key is revoked, each part it keeps opens to nothing, a restore from it
    loses the workspace, and its purge removes every part."""
    infra = VmInfra(tmp_path)
    storage = StorageMemoryImpl()
    managers = build_managers(
        storage,
        infra,
        agent_root_options=AgentRootOptions(
            tool_catalog=stand_ins("read_log"),
            tools_options=ToolsOptions(snapshot_part_bytes=PART),
        ),
    )
    ctx = context(Role.MEMBER)
    unkept, kept = [
        (await managers.agent_sessions.create_agent_session(ctx, make_agent_session())).id
        for _ in range(2)
    ]
    memory_only = StoragePolicy(mode=StorageMode.MEMORY_ONLY, keep_shape=False)
    await managers.privacy.set_policy(ctx, unkept, memory_only)

    async def snapshot(agent_session_id: UUID) -> AgentWorkspaceSnapshot:
        workspace = await managers.tools.prepare_agent_workspace(ctx, agent_session_id, KEPT)
        (infra.directory(agent_session_id) / "notes.txt").write_text(PLAIN)
        epoch = await managers.agent_steps.begin_run(ctx, agent_session_id)
        step = await managers.tools.snapshot_agent_workspace(
            ctx, agent_session_id, workspace, epoch=epoch, loop_id=new_id(), tools=()
        )
        return named(step)

    with pytest.raises(SnapshotRefused, match="no content at rest"):
        await snapshot(unkept)
    assert infra.vms.packs == [], "nothing of the directory was packed"
    assert await infra.stored(ctx.org_id, unkept) == {}

    taken = await snapshot(kept)
    seal = SnapshotSealKeysImpl(
        AgentSessionKeysImpl(storage.get_privacy_storage(), infra.get_keys()),
        storage.get_privacy_storage(),
    )
    blobs = await infra.stored(ctx.org_id, kept)
    manifest = blobs[session_prefix(kept) + taken.hash.removeprefix(HASH_SCHEME)]
    opened = await seal.open(ctx, kept, taken.hash, manifest)
    assert opened is not None
    hashes = [digest for digest, _ in json.loads(opened)["parts"]]
    parts = [blobs[part_key(kept, taken.id, index)] for index in range(len(hashes))]
    pairs = list(zip(hashes, parts, strict=True))
    assert len(pairs) > 2
    assert all([await seal.open(ctx, kept, digest, part) for digest, part in pairs])

    await managers.privacy.revoke_key(ctx, kept)

    assert [await seal.open(ctx, kept, digest, part) for digest, part in pairs] == [None] * len(
        pairs
    ), "noise"
    with pytest.raises(AgentWorkspaceLost, match="erased"):
        await managers.tools.prepare_agent_workspace(ctx, kept, KEPT, restore=taken)
    await managers.tools.purge_agent_workspace(ctx.org_id, kept)
    assert await infra.stored(ctx.org_id, kept) == {}
