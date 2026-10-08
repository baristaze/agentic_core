import asyncio
import errno
import os
import re
import stat
from collections.abc import Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
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
    RecordSeal,
    TransportInterface,
    file_offset,
    relative_path,
    require_mode,
)
from acme.infra.transports.injection import BASE_LANG, injected
from acme.infra.transports.processes import drive, end_group, end_tree, spawn
from acme.infra.transports.records import RecordBook, opened_result, sealed_record
from acme.infra.workspaces import IsolationMode, Workspace
from acme.infra.workspaces.account import (
    SHARED_MODE,
    UMASK,
    end_group_as,
    switch_to,
    temporary,
)

DEFAULT_PATH = "/usr/local/bin:/usr/bin:/bin"
"""The search path of a command's environment, which holds nothing else of
this process's."""

ENTER = 'set -a; eval "$(cat)"; set +a; exec </dev/null; cd -- "$0" && exec "$@"'
"""What runs first as the account: the command's environment, read from its
standard input, so no program that runs with this process's privileges sees
it and no listing of the host's processes shows it; then the command's
directory, entered as the account, so a link there reaches only what the
account may; then the command, with nothing on its standard input."""

VARIABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
FILE = os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY


class TransportLocalImpl(TransportInterface):
    """Runs commands in this process's host, in a workspace that is a
    directory on it: the transport of the host provider. A command is a
    process of its own session, whose environment is built from nothing: the
    search path, the workspace as its home, a locale, the command's own
    variables, and its injected secrets. A file is read without following a
    link at any step, so a caller that polls a path reads the file there and
    nothing a link swapped in. Any other path names a place inside the
    workspace, links resolved, or it is refused.

    Given an account, it is the transport of the account provider
    (`acme.infra.workspaces.account`): each command runs as the account,
    with its temporary directory beside the workspace, what a command left
    is ended as the account, and every path is followed without a link at
    any step, since the account plants what it likes there and this process
    reads and writes with more than it may."""

    def __init__(
        self,
        records: Path,
        secrets: SecretsInterface,
        broker: CredentialBrokerInterface,
        search_path: str = DEFAULT_PATH,
        account: str | None = None,
    ) -> None:
        self._book = RecordBook(records)
        self._secrets = secrets
        self._broker = broker
        self._search_path = search_path
        self._account = account

    async def run(
        self,
        workspace: Workspace,
        command: CommandSpec,
        on_output: OutputSink | None = None,
        *,
        seal: RecordSeal,
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
                env = {"PATH": self._search_path, "HOME": str(root), "LANG": BASE_LANG}
                if self._account is not None:
                    env["TMPDIR"] = str(temporary(root))
                env |= {**dict(command.env), **injection.env}
                try:
                    process = await self._spawned(workspace, command.argv, root, cwd, env)
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
                        end_left=lambda: self._end_left(process.pid),
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
        record = await sealed_record(result, seal)
        await asyncio.to_thread(self._book.record, workspace.id, record)
        return result

    async def outcome(
        self, workspace: Workspace, key: UUID, epoch: int, *, seal: RecordSeal
    ) -> CommandResult | None:
        self._root(workspace)
        await asyncio.to_thread(self._book.admit, workspace.id, epoch)
        record = await asyncio.to_thread(self._book.read, workspace.id, key)
        return None if record is None else await opened_result(record, seal)

    async def purge_records(self, workspace_id: UUID) -> None:
        await asyncio.to_thread(self._book.purge, workspace_id)

    async def _spawned(
        self,
        workspace: Workspace,
        argv: Sequence[str],
        root: Path,
        cwd: Path,
        env: Mapping[str, str],
    ) -> asyncio.subprocess.Process:
        """The command started: as this process, or as the account, its
        environment piped to it after the switch."""
        if self._account is None:
            return await spawn(argv, cwd, env)
        switch = await asyncio.to_thread(switch_to, self._account)
        script = _sourced(env)
        read, write = os.pipe()
        try:
            process = await spawn(
                switch.argv(ENTER, (str(cwd), *argv), workspace.spec.limits),
                root,
                {},
                umask=UMASK,
                stdin=read,
            )
        except BaseException:
            os.close(write)
            raise
        finally:
            os.close(read)
        await asyncio.to_thread(_written, write, script)
        return process

    async def _end_left(self, group: int) -> None:
        """Ends what a command left once its own process is over: as this
        process, or as the account, which reaches the account's processes
        alone, whatever this process may signal."""
        if self._account is None:
            await end_group(group)
            return
        await end_group_as(await asyncio.to_thread(switch_to, self._account), group)

    async def _owners(self) -> frozenset[int]:
        """Whose files a path may reach in an account's workspace."""
        assert self._account is not None
        switch = await asyncio.to_thread(switch_to, self._account)
        return frozenset({os.getuid(), switch.uid})

    async def read_file(
        self, workspace: Workspace, path: str, max_bytes: int, offset: int = 0
    ) -> bytes:
        root = self._root(workspace)
        start = file_offset(offset)
        owners = None if self._account is None else await self._owners()
        with _beneath(path, "file"):
            return await asyncio.to_thread(_read, root, path, max_bytes, start, owners)

    async def write_file(self, workspace: Workspace, path: str, data: bytes, epoch: int) -> None:
        root = self._root(workspace)
        await asyncio.to_thread(self._book.admit, workspace.id, epoch)
        if self._account is not None:
            with _beneath(path, "file"):
                await asyncio.to_thread(_write, root, path, data, await self._owners())
            return
        target = _inside(root, path)

        def write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

        await asyncio.to_thread(write)

    async def list_files(self, workspace: Workspace, path: str, limit: int) -> list[FileEntry]:
        root = self._root(workspace)
        if self._account is not None:
            with _beneath(path, "directory"):
                return await asyncio.to_thread(_listed, root, path, limit)
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
        return "transport=local" if self._account is None else f"transport=local({self._account})"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def _root(self, workspace: Workspace) -> Path:
        mode = IsolationMode.HOST if self._account is None else IsolationMode.ACCOUNT
        require_mode(workspace, mode, "local")
        return Path(workspace.location).resolve()


def _inside(root: Path, path: str) -> Path:
    """The place `path` names inside `root`, links resolved; refused when it
    lands outside."""
    target = (root / relative_path(path)).resolve()
    if not target.is_relative_to(root):
        raise PathOutsideWorkspace(f"{path!r} leads outside the workspace")
    return target


def _sourced(env: Mapping[str, str]) -> bytes:
    """`env` as the lines a shell reads to set it, each value quoted whole,
    so nothing in it runs."""
    lines: list[str] = []
    for name, value in env.items():
        if not VARIABLE.match(name) or "\0" in value:
            raise ValueError(f"{name!r} cannot be set in a command's environment")
        quoted = value.replace("'", "'\\''")
        lines.append(f"{name}='{quoted}'\n")
    return "".join(lines).encode(errors="surrogateescape")


def _written(fd: int, data: bytes) -> None:
    """`data` written whole to `fd`, which is then closed. A reader that
    ended first leaves the rest unread."""
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
    except BrokenPipeError:
        pass
    finally:
        os.close(fd)


@contextmanager
def _beneath(path: str, noun: str) -> Iterator[None]:
    """What a walk that follows no link answers, as the transport says it."""
    try:
        yield
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError) as error:
        raise InfraNotFound(f"no {noun} {path!r} in the workspace") from error
    except OSError as error:
        if error.errno != errno.ELOOP:
            raise
        raise PathOutsideWorkspace(f"{path!r} leads through a link, never followed here") from error


def _walked(root: Path, parts: Sequence[str], *, make: bool = False) -> int:
    """The directory `parts` names below `root`, as a descriptor, reached
    without following a link at any step; each one missing made, shared with
    the account's group, when `make`."""
    folder = os.open(root, DIRECTORY)
    try:
        for part in parts:
            try:
                below = os.open(part, DIRECTORY, dir_fd=folder)
            except NotADirectoryError:
                if stat.S_ISLNK(os.stat(part, dir_fd=folder, follow_symlinks=False).st_mode):
                    raise OSError(errno.ELOOP, "a link", part) from None
                raise
            except FileNotFoundError:
                if not make:
                    raise
                os.mkdir(part, 0o700, dir_fd=folder)
                below = os.open(part, DIRECTORY, dir_fd=folder)
                if os.fstat(below).st_uid == os.getuid():
                    os.fchmod(below, SHARED_MODE)
            os.close(folder)
            folder = below
    except BaseException:
        os.close(folder)
        raise
    return folder


def _file(root: Path, path: str, flags: int, owners: Collection[int] | None) -> int:
    """The regular file `path` names below `root`, open, reached without
    following a link at any step. Where `owners` are named, of one of them:
    never a file of another's that a hard link reaches, and never one of
    this process's with a second link, which may be its own file outside the
    workspace."""
    *parts, name = relative_path(path).parts or ("",)
    if not name:
        raise IsADirectoryError(path)
    folder = _walked(root, parts, make=bool(flags & os.O_CREAT))
    try:
        fd = os.open(name, flags | FILE, 0o660, dir_fd=folder)
    finally:
        os.close(folder)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        os.close(fd)
        raise IsADirectoryError(path)
    linked = info.st_uid == os.getuid() and info.st_nlink > 1
    if owners is not None and (info.st_uid not in owners or linked):
        os.close(fd)
        raise PathOutsideWorkspace(f"{path!r} is not a file of the workspace")
    return fd


def _read(
    root: Path, path: str, max_bytes: int, offset: int, owners: Collection[int] | None
) -> bytes:
    """At most `max_bytes` of the file from `offset`, which is sought, so no
    byte before it is read."""
    with os.fdopen(_file(root, path, os.O_RDONLY, owners), "rb") as handle:
        handle.seek(offset)
        return handle.read(max_bytes)


def _write(root: Path, path: str, data: bytes, owners: Collection[int]) -> None:
    """The file written whole; one this process makes is its group's to
    change too."""
    fd = _file(root, path, os.O_WRONLY | os.O_CREAT, owners)
    with os.fdopen(fd, "wb") as handle:
        os.ftruncate(fd, 0)
        if os.fstat(fd).st_uid == os.getuid():
            os.fchmod(fd, 0o660)
        handle.write(data)


def _listed(root: Path, path: str, limit: int) -> list[FileEntry]:
    relative = relative_path(path)
    folder = _walked(root, relative.parts)
    try:
        with os.scandir(folder) as found:
            entries = sorted(found, key=lambda entry: entry.name)[:limit]
            return [
                FileEntry(
                    path=str(PurePosixPath(relative, entry.name)),
                    is_dir=entry.is_dir(follow_symlinks=False),
                    size=0
                    if entry.is_dir(follow_symlinks=False)
                    else entry.stat(follow_symlinks=False).st_size,
                )
                for entry in entries
            ]
    finally:
        os.close(folder)
