from collections.abc import AsyncIterator
from uuid import UUID

from acme.infra.workspaces import (
    EgressMode,
    IsolationMode,
    IsolationRefused,
    IsolationSpec,
    SnapshotRefused,
    Workspace,
    WorkspaceProviderInterface,
    refusal,
)


class WorkspaceTwinImpl(WorkspaceProviderInterface):
    """The workspace provider's twin, for tests: it prepares the twin mode,
    whose transport runs nothing, so every egress and every limit holds by
    default. It refuses every other mode, as the real ones refuse theirs,
    and remembers which workspaces hold an instance. A workspace's files are
    `files`, its state as bytes, which a test writes: a snapshot answers
    them, a prepare from one sets them, and a prepare from a base sets them
    when the workspace holds none. `bases` holds the base each workspace
    last started from."""

    def __init__(self) -> None:
        self.live: set[UUID] = set()
        self.files: dict[UUID, bytes] = {}
        self.bases: dict[UUID, bytes] = {}

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
        why = refusal(
            spec,
            mode=IsolationMode.TWIN,
            egress=set(EgressMode),
            limits={"cpus", "memory_mb", "processes"},
        )
        built = snapshot is None and spec.base is not None and bool(spec.base.setup)
        if why is None and built and base is None:
            why = "a workspace on a base with setup starts from the base's snapshot alone"
        if why is not None:
            raise IsolationRefused(why)
        self.live.add(workspace_id)
        if snapshot is not None:
            self.files[workspace_id] = snapshot
        elif built and base is not None:
            self.bases[workspace_id] = base
            self.files.setdefault(workspace_id, base)
        return Workspace(
            id=workspace_id, org_id=org_id, spec=spec, location=f"twin:{workspace_id.hex}"
        )

    async def snapshot(self, workspace: Workspace) -> bytes:
        if workspace.id not in self.live:
            raise SnapshotRefused(f"workspace {workspace.id} holds no instance to snapshot")
        return self.files.get(workspace.id, b"")

    async def release(self, workspace: Workspace) -> None:
        self.live.discard(workspace.id)

    async def purge(self, org_id: UUID, workspace_id: UUID) -> None:
        self.live.discard(workspace_id)
        self.files.pop(workspace_id, None)
        self.bases.pop(workspace_id, None)

    async def held(self, snapshot: bytes) -> AsyncIterator[bytes]:
        yield snapshot  # its archive holds its bytes

    async def keep(self, snapshot: bytes, org_id: UUID, workspace_id: UUID) -> bytes:
        return snapshot

    async def discard(self, snapshot: bytes) -> None:
        return None

    async def erase_snapshots(self, org_id: UUID, workspace_id: UUID) -> None:
        return None

    def describe(self) -> str:
        return "workspaces=twin"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None


class WorkspaceNullImpl(WorkspaceProviderInterface):
    """A process that prepares no workspace: every spec is refused, loudly,
    so a session that asks for one learns it before its first model call."""

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
        raise IsolationRefused(
            f"this process prepares no workspace; a {spec.mode.value} workspace is refused"
        )

    async def snapshot(self, workspace: Workspace) -> bytes:
        raise SnapshotRefused("this process prepares no workspace, so it snapshots none")

    async def release(self, workspace: Workspace) -> None:
        return None

    async def purge(self, org_id: UUID, workspace_id: UUID) -> None:
        return None

    async def held(self, snapshot: bytes) -> AsyncIterator[bytes]:
        yield snapshot  # its archive holds its bytes

    async def keep(self, snapshot: bytes, org_id: UUID, workspace_id: UUID) -> bytes:
        return snapshot

    async def discard(self, snapshot: bytes) -> None:
        return None

    async def erase_snapshots(self, org_id: UUID, workspace_id: UUID) -> None:
        return None

    def describe(self) -> str:
        return "workspaces=none"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None
