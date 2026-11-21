import hashlib
import json
import re
import secrets
from collections.abc import AsyncGenerator, AsyncIterable, AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from acme.infra.agent_workspaces import (
    AgentWorkspace,
    AgentWorkspaceLost,
    AgentWorkspaceProviderInterface,
    DirectoryPack,
    EgressMode,
    IsolationMode,
    IsolationRefused,
    IsolationSpec,
    SnapshotRefused,
    packs,
    refusal,
)
from acme.infra.exceptions import BackendFailed, InfraException
from acme.infra.vms import VmsInterface, VmSpec, VmState

WORKSPACE = "workspace"
"""Where a workspace's files sit, from the VM's root: commands start
there, and the transport's paths are under it."""

FORMAT = "acme.vm-snapshot/1"
"""What a VM base's snapshot says it is, in its archive."""

READY = (
    '[ -d "$1" ] || mkdir "$1" 2>/dev/null'
    ' || { sudo -n mkdir -p "$1" && sudo -n chown "$(id -u):$(id -g)" "$1"; }'
)
"""The workspace's folder, made once, the guest user's: a VM's root is
the guest's own, and its user may write only what it is given."""

HASHED = 16
"""Hex digits of a workspace's id in its VM's name: 64 bits of a hash
of the id, so no two workspaces of one store share a name."""

TAG = 8
"""Hex digits that tell a workspace's snapshots apart."""

TURNS = 3
"""The names a workspace's VM takes in turn (`vm_names`). A restore starts
the new VM under the next while the one it replaces stands, so two stand
at most, and of two, the later in turn is a restore's that was cut off
before the one it replaces went."""

PACK_WITHIN = timedelta(minutes=10)
"""How long a pack of a VM's directory may take, by default."""

SNAPSHOT_NAME = re.compile(rf"^[a-z0-9][a-z0-9-]*[0-9a-f]{{{HASHED}}}-[0-9a-f]{{{TAG}}}$")


def vm_name(prefix: str, agent_workspace_id: UUID) -> str:
    """The VM of a workspace: one per workspace, under the prefix,
    named by a hash of its id. A name is short, since a backend may keep a
    socket under it, whose path the host bounds."""
    return prefix + hashlib.sha256(agent_workspace_id.bytes).hexdigest()[:HASHED]


def vm_names(prefix: str, agent_workspace_id: UUID) -> tuple[str, ...]:
    """The names the workspace's VM takes, in turn: its VM name, then that
    name and its turn. No snapshot's name is one of them."""
    name = vm_name(prefix, agent_workspace_id)
    return (name, *(f"{name}-{turn}" for turn in range(1, TURNS)))


def snapshot_name(prefix: str, agent_workspace_id: UUID, tag: str) -> str:
    """A disk kept under a workspace, such as a base's: its VM's name, then
    a tag, so the workspace's purge finds it by that name."""
    return f"{vm_name(prefix, agent_workspace_id)}-{tag}"


def longest_name(prefix: str) -> str:
    """The longest name this provider gives the VMs under `prefix`: a
    snapshot's, which the VMs' probe holds to what they can keep."""
    return snapshot_name(prefix, UUID(int=0), "0" * TAG)


@dataclass(frozen=True)
class Kept:
    """What a VM base's archive names: the tenant it was taken in, the
    snapshot the VMs keep, and the digest its disk is held to."""

    org_id: UUID
    name: str
    digest: str


class AgentWorkspaceVmImpl(AgentWorkspaceProviderInterface):
    """A VM per workspace on the VMs it is given, Docker inside it,
    so a workspace's commands can build and run containers of their own.
    It meets the VM mode with the egress the VMs can close, open
    egress always, and the limits they enforce: whole cpus and memory. An
    allowlist needs an egress proxy this provider does not run, so it is
    refused; so is every spec when the VMs' probe finds no hypervisor.
    There is no weaker place to fall back to. A VM's commands hold its
    guest whole, its root through Docker and `sudo`: the VM is the
    wall, so a base's build (`building`) holds nothing more than any other.

    A released workspace's VM is stopped, its disk kept. Its snapshot
    keeps its directory alone (`keeps_directory`): measured and packed in the
    guest, read out in parts its caller seals under the session's key, and
    unpacked into a new VM on the workspace's base at a restore, so what its
    commands installed outside the directory comes from the base again. The
    VM a restore replaces stands until the new one holds the whole
    directory: one that fails destroys the new VM alone. A base is the one disk
    kept: the setup's VM, stopped and kept by the VMs under the base's own
    workspace (`snapshot`, `keep`), held to a digest at each start on it,
    and destroyed with that workspace's purge. The VMs' store stands for no
    session's seal, only for a base's, so a base with setup is refused
    where that store is not encrypted at rest (ADR 1045)."""

    def __init__(
        self,
        vms: VmsInterface,
        image: str,
        prefix: str,
        timeout: timedelta,
        *,
        pack_within: timedelta = PACK_WITHIN,
    ) -> None:
        self._vms = vms
        self._image = image
        self._prefix = prefix
        self._timeout = timeout
        self._pack_within = pack_within

    async def prepare(
        self,
        org_id: UUID,
        agent_workspace_id: UUID,
        spec: IsolationSpec,
        snapshot: bytes | None = None,
        base: bytes | None = None,
        *,
        building: bool = False,
    ) -> AgentWorkspace:
        vm, kept = await self._ready(org_id, agent_workspace_id, spec, base, building=building)
        if snapshot is not None:
            raise AgentWorkspaceLost(
                "a vm workspace starts from its directory's files, not a whole archive"
            )
        names = vm_names(self._prefix, agent_workspace_id)
        turn = await self._standing(names)
        new = turn is None
        name = names[0 if turn is None else turn]
        # A VM found again keeps its own disk; a new one starts on the
        # base's.
        await self._start(name, vm, kept.name if kept is not None and new else None, new=new)
        return AgentWorkspace(id=agent_workspace_id, org_id=org_id, spec=spec, location=name)

    def keeps_directory(self) -> bool:
        return True

    async def pack(self, workspace: AgentWorkspace, bound: int) -> DirectoryPack:
        name = workspace.location
        if await self._vms.state(name) is not VmState.RUNNING:
            raise SnapshotRefused(f"workspace {workspace.id} holds no instance to snapshot")
        path, size = await packs.pack(
            self._vms.channel(name),
            WORKSPACE,
            bound=bound,
            within=self._pack_within,
            stall=self._timeout,
        )
        return DirectoryPack(location=name, path=path, size=size)

    async def read(self, pack: DirectoryPack, part_bytes: int) -> AsyncGenerator[bytes]:
        channel = self._vms.channel(pack.location)
        async for part in packs.read(channel, pack.path, part_bytes, stall=self._timeout):
            yield part

    async def drop(self, pack: DirectoryPack) -> None:
        # A pack sits in its VM's temporary space: one whose VM no longer
        # runs is gone with it, or goes with its disk.
        if await self._vms.state(pack.location) is VmState.RUNNING:
            await packs.drop(self._vms.channel(pack.location), pack.path, stall=self._timeout)

    async def restore(
        self,
        org_id: UUID,
        agent_workspace_id: UUID,
        spec: IsolationSpec,
        parts: AsyncIterable[bytes],
        base: bytes | None = None,
    ) -> AgentWorkspace:
        vm, kept = await self._ready(org_id, agent_workspace_id, spec, base)
        names = vm_names(self._prefix, agent_workspace_id)
        turn = await self._standing(names)
        # The new VM takes the next name, so the one it replaces stands
        # until the new one holds the whole directory.
        replaced = None if turn is None else names[turn]
        name = names[0 if turn is None else (turn + 1) % TURNS]
        await self._start(name, vm, None if kept is None else kept.name, new=True)
        try:
            await packs.unpack(self._vms.channel(name), WORKSPACE, parts, stall=self._timeout)
            if replaced is not None:
                await self._vms.destroy(replaced)
        except BaseException:
            # The new VM alone goes: no half-restored workspace is ever
            # found again, and the one it would replace stands as it was.
            await self._vms.destroy(name)
            raise
        return AgentWorkspace(id=agent_workspace_id, org_id=org_id, spec=spec, location=name)

    async def snapshot(self, workspace: AgentWorkspace) -> bytes:
        # A base's build alone: its disk, kept by the VMs.
        name = workspace.location
        unkept = self._unkept()
        if unkept is not None:
            raise SnapshotRefused(f"a vm workspace's base cannot be kept: {unkept}")
        if await self._vms.state(name) is not VmState.RUNNING:
            raise SnapshotRefused(f"workspace {workspace.id} holds no instance to snapshot")
        to = await self._unused(workspace.id)
        await self._vms.stop(name)
        try:
            digest = await self._vms.snapshot(name, to)
        finally:
            # The workspace keeps its instance, whether the snapshot held.
            await self._vms.launch(name, self._vm_spec(workspace.spec))
        return _archive(Kept(org_id=workspace.org_id, name=to, digest=digest))

    async def held(self, snapshot: bytes) -> AsyncIterator[bytes]:
        kept = _opened(snapshot, self._prefix)
        async for part in self._vms.read(kept.name):
            yield part

    async def keep(self, snapshot: bytes, org_id: UUID, agent_workspace_id: UUID) -> bytes:
        kept = await self._held_to(snapshot, org_id)
        # Named by what it copies, so a copy asked again is the one made.
        tag = hashlib.sha256(kept.name.encode()).hexdigest()[:TAG]
        to = snapshot_name(self._prefix, agent_workspace_id, tag)
        digest = await self._vms.snapshot(kept.name, to)
        if digest != kept.digest:
            await self._vms.destroy(to)
            raise AgentWorkspaceLost(f"the snapshot {kept.name} changed while it was copied")
        return _archive(Kept(org_id=org_id, name=to, digest=digest))

    async def discard(self, snapshot: bytes) -> None:
        await self._vms.destroy(_opened(snapshot, self._prefix).name)

    async def erase_snapshots(self, org_id: UUID, agent_workspace_id: UUID) -> None:
        # A session's snapshot keeps the directory's files in its sealed
        # parts, and nothing of it here.
        return None

    async def release(self, workspace: AgentWorkspace) -> None:
        await self._vms.stop(workspace.location)

    async def purge(self, org_id: UUID, agent_workspace_id: UUID) -> None:
        name = vm_name(self._prefix, agent_workspace_id)
        for found in await self._vms.names(name):
            if found == name or found.startswith(f"{name}-"):
                await self._vms.destroy(found)

    def describe(self) -> str:
        return f"workspaces=vm({self._image})"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def _unused(self, agent_workspace_id: UUID) -> str:
        """A name for a new snapshot of the workspace that no snapshot
        stands under: the VMs answer a name kept already as the
        snapshot made, so a tag drawn twice would name an older disk."""
        while True:
            to = snapshot_name(self._prefix, agent_workspace_id, secrets.token_hex(TAG // 2))
            if await self._vms.state(to) is VmState.ABSENT:
                return to

    async def _standing(self, names: tuple[str, ...]) -> int | None:
        """The turn of the workspace's VM among `names`; None when none
        stands. Of two, the later in turn is a restore's that was cut off
        before the VM it replaces went, so it may be half-restored: it is
        destroyed, and never found again."""
        found = set(await self._vms.names(names[0]))
        standing = [turn for turn, name in enumerate(names) if name in found]
        later = [turn for turn in standing if (turn - 1) % TURNS in standing]
        if len(later) == len(standing) > 0:
            raise BackendFailed("vm", "find", f"every name of {names[0]} holds a VM")
        for turn in later:
            await self._vms.destroy(names[turn])
        current = [turn for turn in standing if turn not in later]
        return current[0] if current else None

    async def _ready(
        self,
        org_id: UUID,
        agent_workspace_id: UUID,
        spec: IsolationSpec,
        base: bytes | None,
        *,
        building: bool = False,
    ) -> tuple[VmSpec, Kept | None]:
        """What the workspace's VM starts to, and the base disk a new one
        starts on, once the spec is met, the host runs a VM, and the base
        holds its digest; refused, or lost, before anything is made."""
        why = self._refusal(spec, building=building)
        built = spec.base is not None and bool(spec.base.setup)
        if why is None and built and base is None:
            why = "a workspace on a base with setup starts from the base's snapshot alone"
        if why is not None:
            raise IsolationRefused(why, cause=why)
        missing = await self._vms.probe(longest_name(self._prefix))
        if missing is not None:
            raise IsolationRefused(f"a vm workspace needs a VM: {missing}")
        kept = await self._held_to(base, org_id) if built and base is not None else None
        return self._vm_spec(spec), kept

    def _unkept(self) -> str | None:
        """Why a base's disk is not kept here; None when it can be."""
        if self._vms.encrypted_at_rest():
            return None
        return "the store that keeps its disk is not encrypted at rest"

    def _refusal(self, spec: IsolationSpec, *, building: bool = False) -> str | None:
        egress = {EgressMode.OPEN}
        if self._vms.closes_egress():
            egress.add(EgressMode.NONE)
        why = refusal(spec, mode=IsolationMode.VM, egress=egress, limits=self._vms.limits())
        cpus = spec.limits.cpus
        if why is None and cpus is not None and not float(cpus).is_integer():
            why = f"a vm workspace holds whole cpus, not {cpus}"
        unkept = self._unkept()
        if why is None and unkept is not None:
            if building:
                why = f"a vm workspace's base cannot be kept: {unkept}"
            elif spec.base is not None and spec.base.setup:
                why = f"a vm workspace cannot start from a base with setup: {unkept}"
        return why

    def _vm_spec(self, spec: IsolationSpec) -> VmSpec:
        """What the workspace's VM is started to: the base's image or
        this provider's own, and the spec's limits and egress."""
        cpus = spec.limits.cpus
        return VmSpec(
            image=self._image if spec.base is None else spec.base.image,
            cpus=None if cpus is None else int(cpus),
            memory_mb=spec.limits.memory_mb,
            egress_open=spec.egress.mode is EgressMode.OPEN,
        )

    async def _start(self, name: str, vm: VmSpec, snapshot: str | None, *, new: bool) -> None:
        """The VM running to `VM`, on a copy of `snapshot` when it
        is made, with the workspace's folder in it. A VM this start
        made, and that fails part way, is destroyed: no half-made workspace
        is ever found again. One that stood keeps its disk."""
        try:
            await self._vms.launch(name, vm, snapshot)
            ready = await self._vms.run(
                name, ("sh", "-c", READY, "sh", WORKSPACE), bound=self._timeout
            )
            if not ready.ok:
                raise BackendFailed("vm", "ready", ready.reason())
        except BaseException:
            if new:
                await self._vms.destroy(name)
            raise

    async def _held_to(self, snapshot: bytes, org_id: UUID) -> Kept:
        """What a base's archive names, once the disk it names is found and
        holds its digest. Anything else loses the workspace that would start
        from it (`AgentWorkspaceLost`)."""
        kept = _opened(snapshot, self._prefix)
        if kept.org_id != org_id:
            raise AgentWorkspaceLost("the snapshot was taken in another tenant")
        try:
            digest = await self._vms.digest(kept.name)
        except InfraException as error:
            raise AgentWorkspaceLost(f"the snapshot {kept.name} cannot be read: {error}") from error
        if digest is None:
            raise AgentWorkspaceLost(f"the snapshot {kept.name} is gone from the VMs' store")
        if digest != kept.digest:
            raise AgentWorkspaceLost(f"the snapshot {kept.name} does not match its digest")
        return kept


def _archive(kept: Kept) -> bytes:
    """A VM base's archive: what it names, never the disk."""
    named = {"format": FORMAT, "org": str(kept.org_id), "name": kept.name, "digest": kept.digest}
    return json.dumps(named, sort_keys=True).encode()


def _opened(snapshot: bytes, prefix: str) -> Kept:
    """What `_archive` wrote. Anything else, such as another provider's
    snapshot, is no workspace this provider can bring in."""
    try:
        named = json.loads(snapshot)
        format_, name, digest = named["format"], named["name"], named["digest"]
        org_id = UUID(named["org"])
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise AgentWorkspaceLost(f"the snapshot is no vm's: {error}") from error
    if format_ != FORMAT:
        raise AgentWorkspaceLost(f"the snapshot is {format_!r}, not a vm's")
    if not isinstance(name, str) or not name.startswith(prefix) or not SNAPSHOT_NAME.match(name):
        raise AgentWorkspaceLost("the snapshot names no VM snapshot of this provider")
    if not isinstance(digest, str) or not digest:
        raise AgentWorkspaceLost("the snapshot names no digest")
    return Kept(org_id=org_id, name=name, digest=digest)
