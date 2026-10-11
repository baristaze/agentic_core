import hashlib
import json
import re
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from acme.infra.exceptions import BackendFailed, InfraException
from acme.infra.machines import MachinesInterface, MachineSpec, MachineState
from acme.infra.workspaces import (
    EgressMode,
    IsolationMode,
    IsolationRefused,
    IsolationSpec,
    SnapshotRefused,
    Workspace,
    WorkspaceLost,
    WorkspaceProviderInterface,
    refusal,
)

WORKSPACE = "workspace"
"""Where a workspace's files sit, from the machine's root: commands start
there, and the transport's paths are under it."""

FORMAT = "acme.vm-snapshot/1"
"""What a VM's snapshot says it is, in its archive."""

READY = (
    '[ -d "$1" ] || mkdir "$1" 2>/dev/null'
    ' || { sudo -n mkdir -p "$1" && sudo -n chown "$(id -u):$(id -g)" "$1"; }'
)
"""The workspace's folder, made once, the guest user's: a machine's root is
the guest's own, and its user may write only what it is given."""

HASHED = 16
"""Hex digits of a workspace's id in its machine's name: 64 bits of a hash
of the id, so no two workspaces of one store share a name."""

TAG = 8
"""Hex digits that tell a workspace's snapshots apart."""

SNAPSHOT_NAME = re.compile(rf"^[a-z0-9][a-z0-9-]*[0-9a-f]{{{HASHED}}}-[0-9a-f]{{{TAG}}}$")


def machine_name(prefix: str, workspace_id: UUID) -> str:
    """The machine of a workspace: one per workspace, under the prefix,
    named by a hash of its id. A name is short, since a backend may keep a
    socket under it, whose path the host bounds."""
    return prefix + hashlib.sha256(workspace_id.bytes).hexdigest()[:HASHED]


def snapshot_name(prefix: str, workspace_id: UUID, tag: str) -> str:
    """A snapshot kept under a workspace: its machine's name, then a tag, so
    the workspace's purge finds it by that name."""
    return f"{machine_name(prefix, workspace_id)}-{tag}"


def longest_name(prefix: str) -> str:
    """The longest name this provider gives the machines under `prefix`: a
    snapshot's, which the machines' probe holds to what they can keep."""
    return snapshot_name(prefix, UUID(int=0), "0" * TAG)


@dataclass(frozen=True)
class Kept:
    """What a VM's archive names: the tenant it was taken in, the snapshot
    the machines keep, and the digest its disk is held to."""

    org_id: UUID
    name: str
    digest: str


class WorkspaceVmImpl(WorkspaceProviderInterface):
    """A machine per workspace on the machines it is given, Docker inside it,
    so a workspace's commands can build and run containers of their own.
    It meets the VM mode with the egress the machines can close, open
    egress always, and the limits they enforce: whole cpus and memory. An
    allowlist needs an egress proxy this provider does not run, so it is
    refused; so is every spec when the machines' probe finds no hypervisor.
    There is no weaker place to fall back to. A machine's commands hold its
    guest whole, its root through Docker and `sudo`: the machine is the
    wall, so a base's build (`building`) holds nothing more than any other.

    A released workspace's machine is stopped, its disk kept. Its snapshot
    is its disk: the machine is stopped, so nothing writes, its disk is
    kept by the machines as a snapshot under the workspace's machine name,
    and the machine starts again. The archive names that snapshot, its
    digest, and the tenant: a few hundred bytes, never the disk. A restore
    holds the disk to the digest before the machine it replaces goes, and
    starts a machine on a copy of it. A purge destroys the machine and
    every snapshot kept under it, and a revocation of the session's key
    destroys those snapshots (`erase_snapshots`); a fork's copy and a base
    are kept under their own workspace (`keep`), so neither reaches them.
    The machines' store stands for the session key's seal, so a workspace
    keeps no snapshot where it is not encrypted at rest (ADR 1029)."""

    def __init__(
        self, machines: MachinesInterface, image: str, prefix: str, timeout: timedelta
    ) -> None:
        self._machines = machines
        self._image = image
        self._prefix = prefix
        self._timeout = timeout

    async def prepare(
        self,
        org_id: UUID,
        workspace_id: UUID,
        spec: IsolationSpec,
        snapshot: bytes | None = None,
        base: bytes | None = None,
        *,
        building: bool = False,
    ) -> Workspace:
        why = self._refusal(spec)
        built = snapshot is None and spec.base is not None and bool(spec.base.setup)
        if why is None and built and base is None:
            why = "a workspace on a base with setup starts from the base's snapshot alone"
        if why is not None:
            raise IsolationRefused(why)
        missing = await self._machines.probe(longest_name(self._prefix))
        if missing is not None:
            raise IsolationRefused(f"a vm workspace needs a machine: {missing}")
        name = machine_name(self._prefix, workspace_id)
        workspace = Workspace(id=workspace_id, org_id=org_id, spec=spec, location=name)
        machine = self._machine_spec(spec)
        if snapshot is not None:
            # The disk is held to its digest before the machine it replaces
            # goes: one gone or altered loses the workspace, not its files.
            kept = await self._held_to(snapshot, org_id)
            await self._machines.destroy(name)
            await self._start(name, machine, kept.name, new=True)
        elif built and base is not None:
            kept = await self._held_to(base, org_id)
            new = await self._machines.state(name) is MachineState.ABSENT
            # A machine found again keeps its own disk; a new one starts on
            # the base's.
            await self._start(name, machine, kept.name if new else None, new=new)
        else:
            new = await self._machines.state(name) is MachineState.ABSENT
            await self._start(name, machine, None, new=new)
        return workspace

    async def snapshot(self, workspace: Workspace) -> bytes:
        name = workspace.location
        unkept = self._unkept()
        if unkept is not None:
            raise SnapshotRefused(f"a vm workspace cannot be snapshotted: {unkept}")
        if await self._machines.state(name) is not MachineState.RUNNING:
            raise SnapshotRefused(f"workspace {workspace.id} holds no instance to snapshot")
        to = await self._unused(workspace.id)
        await self._machines.stop(name)
        try:
            digest = await self._machines.snapshot(name, to)
        finally:
            # The workspace keeps its instance, whether the snapshot held.
            await self._machines.launch(name, self._machine_spec(workspace.spec))
        return _archive(Kept(org_id=workspace.org_id, name=to, digest=digest))

    async def held(self, snapshot: bytes) -> AsyncIterator[bytes]:
        kept = _opened(snapshot, self._prefix)
        async for part in self._machines.read(kept.name):
            yield part

    async def keep(self, snapshot: bytes, org_id: UUID, workspace_id: UUID) -> bytes:
        kept = await self._held_to(snapshot, org_id)
        # Named by what it copies, so a copy asked again is the one made.
        tag = hashlib.sha256(kept.name.encode()).hexdigest()[:TAG]
        to = snapshot_name(self._prefix, workspace_id, tag)
        digest = await self._machines.snapshot(kept.name, to)
        if digest != kept.digest:
            await self._machines.destroy(to)
            raise WorkspaceLost(f"the snapshot {kept.name} changed while it was copied")
        return _archive(Kept(org_id=org_id, name=to, digest=digest))

    async def discard(self, snapshot: bytes) -> None:
        await self._machines.destroy(_opened(snapshot, self._prefix).name)

    async def erase_snapshots(self, org_id: UUID, workspace_id: UUID) -> None:
        # Every snapshot is named under its workspace's machine, a hyphen,
        # and its tag; the machine itself stands under the name alone.
        for found in await self._machines.names(f"{machine_name(self._prefix, workspace_id)}-"):
            await self._machines.destroy(found)

    async def release(self, workspace: Workspace) -> None:
        await self._machines.stop(workspace.location)

    async def purge(self, org_id: UUID, workspace_id: UUID) -> None:
        name = machine_name(self._prefix, workspace_id)
        for found in await self._machines.names(name):
            if found == name or found.startswith(f"{name}-"):
                await self._machines.destroy(found)

    def describe(self) -> str:
        return f"workspaces=vm({self._image})"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def _unused(self, workspace_id: UUID) -> str:
        """A name for a new snapshot of the workspace that no snapshot
        stands under: the machines answer a name kept already as the
        snapshot made, so a tag drawn twice would name an older disk."""
        while True:
            to = snapshot_name(self._prefix, workspace_id, secrets.token_hex(TAG // 2))
            if await self._machines.state(to) is MachineState.ABSENT:
                return to

    def _unkept(self) -> str | None:
        """Why a workspace here keeps no snapshot; None when it can."""
        if self._machines.encrypted_at_rest():
            return None
        return "the store that keeps its disk is not encrypted at rest"

    def _refusal(self, spec: IsolationSpec) -> str | None:
        egress = {EgressMode.OPEN}
        if self._machines.closes_egress():
            egress.add(EgressMode.NONE)
        why = refusal(
            spec,
            mode=IsolationMode.VM,
            egress=egress,
            limits=self._machines.limits(),
            unkept=self._unkept(),
        )
        cpus = spec.limits.cpus
        if why is None and cpus is not None and not float(cpus).is_integer():
            why = f"a vm workspace holds whole cpus, not {cpus}"
        return why

    def _machine_spec(self, spec: IsolationSpec) -> MachineSpec:
        """What the workspace's machine is started to: the base's image or
        this provider's own, and the spec's limits and egress."""
        cpus = spec.limits.cpus
        return MachineSpec(
            image=self._image if spec.base is None else spec.base.image,
            cpus=None if cpus is None else int(cpus),
            memory_mb=spec.limits.memory_mb,
            egress_open=spec.egress.mode is EgressMode.OPEN,
        )

    async def _start(
        self, name: str, machine: MachineSpec, snapshot: str | None, *, new: bool
    ) -> None:
        """The machine running to `machine`, on a copy of `snapshot` when it
        is made, with the workspace's folder in it. A machine this start
        made, and that fails part way, is destroyed: no half-made workspace
        is ever found again. One that stood keeps its disk."""
        try:
            await self._machines.launch(name, machine, snapshot)
            ready = await self._machines.run(
                name, ("sh", "-c", READY, "sh", WORKSPACE), bound=self._timeout
            )
            if not ready.ok:
                raise BackendFailed("machine", "ready", ready.reason())
        except BaseException:
            if new:
                await self._machines.destroy(name)
            raise

    async def _held_to(self, snapshot: bytes, org_id: UUID) -> Kept:
        """What an archive names, once the disk it names is found and holds
        its digest. Anything else loses the workspace that would start from
        it (`WorkspaceLost`)."""
        kept = _opened(snapshot, self._prefix)
        if kept.org_id != org_id:
            raise WorkspaceLost("the snapshot was taken in another tenant")
        try:
            digest = await self._machines.digest(kept.name)
        except InfraException as error:
            raise WorkspaceLost(f"the snapshot {kept.name} cannot be read: {error}") from error
        if digest is None:
            raise WorkspaceLost(f"the snapshot {kept.name} is gone from the machines' store")
        if digest != kept.digest:
            raise WorkspaceLost(f"the snapshot {kept.name} does not match its digest")
        return kept


def _archive(kept: Kept) -> bytes:
    """A VM snapshot's bytes: what it names, never the disk."""
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
        raise WorkspaceLost(f"the snapshot is no vm's: {error}") from error
    if format_ != FORMAT:
        raise WorkspaceLost(f"the snapshot is {format_!r}, not a vm's")
    if not isinstance(name, str) or not name.startswith(prefix) or not SNAPSHOT_NAME.match(name):
        raise WorkspaceLost("the snapshot names no machine snapshot of this provider")
    if not isinstance(digest, str) or not digest:
        raise WorkspaceLost("the snapshot names no digest")
    return Kept(org_id=org_id, name=name, digest=digest)
