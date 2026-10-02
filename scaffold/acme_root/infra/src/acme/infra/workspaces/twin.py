from uuid import UUID

from acme.infra.workspaces import (
    EgressMode,
    IsolationMode,
    IsolationRefused,
    IsolationSpec,
    Workspace,
    WorkspaceProviderInterface,
    refusal,
)


class WorkspaceTwinImpl(WorkspaceProviderInterface):
    """The workspace provider's twin, for tests: it prepares the twin mode,
    whose transport runs nothing, so every egress and every limit holds by
    default. It refuses every other mode, as the real ones refuse theirs,
    and remembers which workspaces hold an instance."""

    def __init__(self) -> None:
        self.live: set[UUID] = set()

    async def prepare(self, org_id: UUID, workspace_id: UUID, spec: IsolationSpec) -> Workspace:
        why = refusal(
            spec,
            mode=IsolationMode.TWIN,
            egress=set(EgressMode),
            limits={"cpus", "memory_mb", "processes"},
        )
        if why is not None:
            raise IsolationRefused(why)
        self.live.add(workspace_id)
        return Workspace(
            id=workspace_id, org_id=org_id, spec=spec, location=f"twin:{workspace_id.hex}"
        )

    async def release(self, workspace: Workspace) -> None:
        self.live.discard(workspace.id)

    async def purge(self, org_id: UUID, workspace_id: UUID) -> None:
        self.live.discard(workspace_id)

    def describe(self) -> str:
        return "workspaces=twin"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None


class WorkspaceNullImpl(WorkspaceProviderInterface):
    """A process that prepares no workspace: every spec is refused, loudly,
    so a session that asks for one learns it before its first model call."""

    async def prepare(self, org_id: UUID, workspace_id: UUID, spec: IsolationSpec) -> Workspace:
        raise IsolationRefused(
            f"this process prepares no workspace; a {spec.mode.value} workspace is refused"
        )

    async def release(self, workspace: Workspace) -> None:
        return None

    async def purge(self, org_id: UUID, workspace_id: UUID) -> None:
        return None

    def describe(self) -> str:
        return "workspaces=none"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None
