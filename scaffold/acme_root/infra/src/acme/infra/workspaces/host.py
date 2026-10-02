import asyncio
import shutil
from pathlib import Path
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
from acme.infra.workspaces.stragglers import end_stragglers


class WorkspaceHostImpl(WorkspaceProviderInterface):
    """A directory on this host, one per workspace, under `root`. A directory
    confines where files go and nothing else: not a process's network, and
    not what it takes of the machine. So it meets the host mode with open
    egress and no resource limit, and refuses any spec that asks for more.
    Its instance is the processes running in it: a release ends what its
    commands left running there, and keeps the files."""

    def __init__(self, root: Path) -> None:
        self._root = root

    async def prepare(self, org_id: UUID, workspace_id: UUID, spec: IsolationSpec) -> Workspace:
        why = refusal(spec, mode=IsolationMode.HOST, egress={EgressMode.OPEN}, limits=())
        if why is not None:
            raise IsolationRefused(why)
        directory = self._directory(org_id, workspace_id)
        await asyncio.to_thread(directory.mkdir, mode=0o700, parents=True, exist_ok=True)
        return Workspace(id=workspace_id, org_id=org_id, spec=spec, location=str(directory))

    async def release(self, workspace: Workspace) -> None:
        await end_stragglers(self._directory(workspace.org_id, workspace.id))

    async def purge(self, org_id: UUID, workspace_id: UUID) -> None:
        directory = self._directory(org_id, workspace_id)
        await end_stragglers(directory)
        await asyncio.to_thread(_removed, directory)

    def describe(self) -> str:
        return f"workspaces=host({self._root})"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def _directory(self, org_id: UUID, workspace_id: UUID) -> Path:
        """Named by ids alone, so no name climbs out of the root."""
        return self._root.resolve() / org_id.hex / workspace_id.hex


def _removed(directory: Path) -> None:
    """The directory and everything in it gone; one already gone is no
    error, and a file that cannot be removed is."""
    try:
        shutil.rmtree(directory)
    except FileNotFoundError:
        return
