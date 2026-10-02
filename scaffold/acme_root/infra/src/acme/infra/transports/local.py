import asyncio
from pathlib import Path
from uuid import UUID

from acme.infra.base import utcnow
from acme.infra.exceptions import InfraNotFound
from acme.infra.secrets import SecretsInterface
from acme.infra.transports import (
    CommandResult,
    CommandSpec,
    CredentialBrokerInterface,
    FileEntry,
    OutputSink,
    PathOutsideWorkspace,
    TransportInterface,
    relative_path,
    require_mode,
)
from acme.infra.transports.injection import BASE_LANG, injected
from acme.infra.transports.processes import drive, end_tree, spawn
from acme.infra.transports.records import RecordBook
from acme.infra.workspaces import IsolationMode, Workspace

DEFAULT_PATH = "/usr/local/bin:/usr/bin:/bin"
"""The search path of a command's environment, which holds nothing else of
this process's."""


class TransportLocalImpl(TransportInterface):
    """Runs commands in this process's host, in a workspace that is a
    directory on it: the transport of the host provider. A command is a
    process of its own session, whose environment is built from nothing: the
    search path, the workspace as its home, a locale, the command's own
    variables, and its injected secrets. A path names a place inside the
    workspace, links resolved, or it is refused."""

    def __init__(
        self,
        records: Path,
        secrets: SecretsInterface,
        broker: CredentialBrokerInterface,
        search_path: str = DEFAULT_PATH,
    ) -> None:
        self._book = RecordBook(records)
        self._secrets = secrets
        self._broker = broker
        self._search_path = search_path

    async def run(
        self, workspace: Workspace, command: CommandSpec, on_output: OutputSink | None = None
    ) -> CommandResult:
        root = self._root(workspace)
        await asyncio.to_thread(self._book.admit, workspace.id, command.epoch)
        cwd = _inside(root, command.cwd)
        if not cwd.is_dir():
            raise InfraNotFound(f"no directory {command.cwd!r} in the workspace")
        if utcnow() >= command.deadline:
            result = CommandResult(key=command.key, exit_code=None, timed_out=True)
        else:
            async with injected(self._secrets, self._broker, workspace, command) as injection:
                env = {
                    "PATH": self._search_path,
                    "HOME": str(root),
                    "LANG": BASE_LANG,
                    **dict(command.env),
                    **injection.env,
                }
                try:
                    process = await spawn(command.argv, cwd, env)
                except FileNotFoundError, PermissionError:
                    # What a shell answers for a program it cannot run.
                    result = CommandResult(
                        key=command.key,
                        exit_code=127,
                        stderr=f"{command.argv[0]}: cannot be run",
                        secrets=injection.names,
                    )
                else:
                    driven = await drive(
                        process,
                        injection.redactor,
                        max_output=command.max_output,
                        deadline=command.deadline,
                        on_output=on_output,
                        end=lambda: end_tree(process.pid),
                    )
                    result = CommandResult(
                        key=command.key,
                        exit_code=driven.exit_code,
                        stdout=driven.stdout,
                        stderr=driven.stderr,
                        timed_out=driven.timed_out,
                        truncated=driven.truncated,
                        secrets=injection.names,
                    )
        await asyncio.to_thread(self._book.record, workspace.id, result)
        return result

    async def outcome(self, workspace: Workspace, key: UUID, epoch: int) -> CommandResult | None:
        self._root(workspace)
        await asyncio.to_thread(self._book.admit, workspace.id, epoch)
        return await asyncio.to_thread(self._book.read, workspace.id, key)

    async def read_file(self, workspace: Workspace, path: str, max_bytes: int) -> bytes:
        target = _inside(self._root(workspace), path)

        def read() -> bytes:
            try:
                with target.open("rb") as handle:
                    return handle.read(max_bytes)
            except (FileNotFoundError, IsADirectoryError) as error:
                raise InfraNotFound(f"no file {path!r} in the workspace") from error

        return await asyncio.to_thread(read)

    async def write_file(self, workspace: Workspace, path: str, data: bytes, epoch: int) -> None:
        root = self._root(workspace)
        await asyncio.to_thread(self._book.admit, workspace.id, epoch)
        target = _inside(root, path)

        def write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

        await asyncio.to_thread(write)

    async def list_files(self, workspace: Workspace, path: str, limit: int) -> list[FileEntry]:
        root = self._root(workspace)
        folder = _inside(root, path)

        def listing() -> list[FileEntry]:
            if not folder.is_dir():
                raise InfraNotFound(f"no directory {path!r} in the workspace")
            entries = sorted(folder.iterdir())[:limit]
            return [
                FileEntry(
                    path=str(entry.relative_to(root)),
                    is_dir=entry.is_dir(),
                    size=0 if entry.is_dir() else entry.lstat().st_size,
                )
                for entry in entries
            ]

        return await asyncio.to_thread(listing)

    def describe(self) -> str:
        return "transport=local"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def _root(self, workspace: Workspace) -> Path:
        require_mode(workspace, IsolationMode.HOST, "local")
        return Path(workspace.location).resolve()


def _inside(root: Path, path: str) -> Path:
    """The place `path` names inside `root`, links resolved; refused when it
    lands outside."""
    target = (root / relative_path(path)).resolve()
    if not target.is_relative_to(root):
        raise PathOutsideWorkspace(f"{path!r} leads outside the workspace")
    return target
