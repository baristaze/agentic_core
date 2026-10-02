"""What a transport keeps of each workspace on the host it runs on: the
highest writer epoch it has admitted, and how each command ended, under the
command's key. Files beside the workspaces, never inside one, so a new run
on this host reads them after a crash, and two processes on one host fence
each other through the same file.

A record keeps the outcome in the clear, what recovery decides by, and the
output sealed by the seal its command came with (`RecordSealInterface`).
The twin keeps its records in memory, in the same form."""

import base64
import fcntl
import json
import logging
import os
import shutil
from pathlib import Path
from uuid import UUID

from acme.infra.base import InfraModel
from acme.infra.transports import CommandResult, RecordSealInterface, StaleCommand

log = logging.getLogger(__name__)


class CommandRecord(InfraModel):
    """How a command ended, as its transport keeps it. The outcome is in the
    clear; `output`, its stdout and its stderr, is sealed (URL-safe base64),
    or None when its session keeps no content at rest."""

    key: UUID
    exit_code: int | None
    timed_out: bool = False
    truncated: bool = False
    secrets: tuple[str, ...] = ()
    output: str | None = None


async def sealed_record(result: CommandResult, seal: RecordSealInterface) -> CommandRecord:
    """The record of `result`, its output sealed. A seal that fails costs the
    record its output, never the result of a command that ran: recovery
    then reads how the command ended without what it printed."""
    plain = json.dumps({"stdout": result.stdout, "stderr": result.stderr}).encode()
    try:
        blob = await seal.seal(result.key, plain)
    except Exception:
        log.warning("command %s is recorded without its output", result.key, exc_info=True)
        blob = None
    return CommandRecord(
        key=result.key,
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        truncated=result.truncated,
        secrets=result.secrets,
        output=None if blob is None else base64.urlsafe_b64encode(blob).decode(),
    )


async def opened_result(record: CommandRecord, seal: RecordSealInterface) -> CommandResult:
    """How the command ended, its output opened by `seal`; an empty output
    when the record kept none, or the key it was sealed under is gone."""
    result = CommandResult(
        key=record.key,
        exit_code=record.exit_code,
        timed_out=record.timed_out,
        truncated=record.truncated,
        secrets=record.secrets,
    )
    if record.output is None:
        return result
    plain = await seal.open(record.key, base64.urlsafe_b64decode(record.output.encode()))
    if plain is None:
        return result
    output = json.loads(plain)
    return result.model_copy(update={"stdout": output["stdout"], "stderr": output["stderr"]})


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

    def record(self, workspace_id: UUID, record: CommandRecord) -> None:
        """Writes how a command ended, whole or not at all."""
        folder = self._folder(workspace_id)
        partial = folder / f"{record.key}.partial"
        partial.write_text(record.model_dump_json())
        os.replace(partial, folder / f"{record.key}.json")

    def read(self, workspace_id: UUID, key: UUID) -> CommandRecord | None:
        path = self._root / workspace_id.hex / f"{key}.json"
        if not path.is_file():
            return None
        return CommandRecord.model_validate_json(path.read_text())

    def purge(self, workspace_id: UUID) -> None:
        """Removes every record of the workspace and its epoch. A workspace
        with none is no error; a record that cannot be removed is."""
        try:
            shutil.rmtree(self._root / workspace_id.hex)
        except FileNotFoundError:
            return

    def _folder(self, workspace_id: UUID) -> Path:
        folder = self._root / workspace_id.hex
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        return folder
