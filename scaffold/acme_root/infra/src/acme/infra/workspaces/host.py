import asyncio
import os
import shutil
import stat
from collections.abc import AsyncIterator, Callable
from pathlib import Path
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
from acme.infra.workspaces.stragglers import end_stragglers

UNKEPT = "its commands write outside its directory, so no snapshot of it holds all they wrote"
"""Why a directory on a host is never snapshotted: what a command installs or
leaves in the host's home, its temporary directory, or its packages sits
outside the directory, and a snapshot of the directory alone would lose it
without a word."""


class WorkspaceHostImpl(WorkspaceProviderInterface):
    """A directory on this host, one per workspace, under `root`. A directory
    confines where files go and nothing else: not a process's network, and
    not what it takes of the machine. So it meets the host mode with open
    egress and no resource limit, and refuses any spec that asks for more.
    Its instance is the processes running in it: a release ends what its
    commands left running there, and keeps the files. It never snapshots a
    workspace (`UNKEPT`): a spec that asks for one is refused, and so is a
    prepare from a snapshot."""

    def __init__(self, root: Path) -> None:
        self._root = root

    async def prepare(
        self,
        org_id: UUID,
        workspace_id: UUID,
        spec: IsolationSpec,
        snapshot: bytes | None = None,
        base: bytes | None = None,
    ) -> Workspace:
        why = refusal(
            spec, mode=IsolationMode.HOST, egress={EgressMode.OPEN}, limits=(), unkept=UNKEPT
        )
        if why is None and (snapshot is not None or base is not None):
            why = f"a host workspace cannot start from a snapshot: {UNKEPT}"
        if why is not None:
            raise IsolationRefused(why)
        directory = self._directory(org_id, workspace_id)
        await asyncio.to_thread(directory.mkdir, mode=0o700, parents=True, exist_ok=True)
        return Workspace(id=workspace_id, org_id=org_id, spec=spec, location=str(directory))

    async def snapshot(self, workspace: Workspace) -> bytes:
        raise SnapshotRefused(f"a host workspace cannot be snapshotted: {UNKEPT}")

    async def release(self, workspace: Workspace) -> None:
        await end_stragglers(self._directory(workspace.org_id, workspace.id))

    async def purge(self, org_id: UUID, workspace_id: UUID) -> None:
        directory = self._directory(org_id, workspace_id)
        await end_stragglers(directory)
        await asyncio.to_thread(remove_directory, directory)

    async def held(self, snapshot: bytes) -> AsyncIterator[bytes]:
        yield snapshot  # it makes no snapshot, so none names bytes kept elsewhere

    async def keep(self, snapshot: bytes, org_id: UUID, workspace_id: UUID) -> bytes:
        return snapshot

    def describe(self) -> str:
        return f"workspaces=host({self._root})"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def _directory(self, org_id: UUID, workspace_id: UUID) -> Path:
        """Named by ids alone, so no name climbs out of the root."""
        return self._root.resolve() / org_id.hex / workspace_id.hex


def remove_directory(directory: Path) -> None:
    """The directory and everything in it gone, a directory a command left
    read-only, or unreadable, included, as a module cache leaves its own.
    One already gone is no error, and a file that still cannot be removed
    is."""
    try:
        shutil.rmtree(directory, onexc=_given_back)
    except FileNotFoundError:
        return


def _given_back(function: Callable[..., object], path: str, error: BaseException) -> None:
    """What `rmtree` could not remove for want of a permission: the
    directory that holds it, and it when it is a directory, are given back
    to their owner, and the removal is tried again. A link is never
    followed, so nothing outside the workspace changes."""
    if isinstance(error, FileNotFoundError):
        return
    if not isinstance(error, PermissionError):
        raise error
    for place in (os.path.dirname(path), path):
        if os.path.isdir(place) and not os.path.islink(place):
            os.chmod(place, stat.S_IRWXU)
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path, onexc=_given_back)
    else:
        function(path)
