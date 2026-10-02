"""What a transport keeps of each workspace on the host it runs on: the
highest writer epoch it has admitted, and how each command ended, under the
command's key. Files beside the workspaces, never inside one, so a new run
on this host reads them after a crash, and two processes on one host fence
each other through the same file."""

import fcntl
import os
from pathlib import Path
from uuid import UUID

from acme.infra.transports import CommandResult, StaleCommand


class RecordBook:
    def __init__(self, root: Path) -> None:
        self._root = root

    def admit(self, workspace_id: UUID, epoch: int) -> None:
        """Admits a command under `epoch`, and raises `StaleCommand` when this
        workspace has admitted a higher one. One lock on the workspace's
        epoch file holds the read and the write together."""
        folder = self._folder(workspace_id)
        with open(folder / "epoch", "a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.seek(0)
            seen = int(handle.read().strip() or "0")
            if epoch < seen:
                raise StaleCommand(
                    f"workspace {workspace_id} runs commands at epoch {seen}; {epoch} is stale"
                )
            handle.seek(0)
            handle.truncate()
            handle.write(str(epoch))

    def record(self, workspace_id: UUID, result: CommandResult) -> None:
        """Writes how a command ended, whole or not at all."""
        folder = self._folder(workspace_id)
        partial = folder / f"{result.key}.partial"
        partial.write_text(result.model_dump_json())
        os.replace(partial, folder / f"{result.key}.json")

    def read(self, workspace_id: UUID, key: UUID) -> CommandResult | None:
        path = self._root / workspace_id.hex / f"{key}.json"
        if not path.is_file():
            return None
        return CommandResult.model_validate_json(path.read_text())

    def _folder(self, workspace_id: UUID) -> Path:
        folder = self._root / workspace_id.hex
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        return folder
