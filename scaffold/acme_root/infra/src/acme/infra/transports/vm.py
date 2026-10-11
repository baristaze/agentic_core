import asyncio
import base64
from collections.abc import Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from acme.infra.base import utcnow
from acme.infra.exceptions import BackendFailed, InfraNotFound
from acme.infra.machines import MachinesInterface
from acme.infra.secrets import SecretsInterface
from acme.infra.transports import (
    CommandResult,
    CommandSpec,
    CredentialBrokerInterface,
    FileEntry,
    OutputSink,
    RecordSeal,
    TransportInterface,
    file_offset,
    relative_path,
    require_mode,
)
from acme.infra.transports.container import END_TREE, READ_FROM
from acme.infra.transports.injection import BASE_LANG, injected
from acme.infra.transports.processes import drive, end_tree, spawn
from acme.infra.transports.records import RecordBook, opened_result, sealed_record
from acme.infra.workspaces import IsolationMode, Workspace
from acme.infra.workspaces.vm import WORKSPACE

LAUNCHER = r"""
pidfile=$1 folder=$2
shift 2
root=${PWD%/}
while IFS= read -r line && [ -n "$line" ]; do
    value=$(printf '%s' "${line#*=}" | base64 -d; printf x)
    export "${line%%=*}=${value%x}"
done
case $HOME in /*) ;; *) export HOME="$root/$HOME" ;; esac
echo $$ > "$pidfile"
cd "$folder" || exit 126
exec "$@" < /dev/null
"""
"""Reads the command's variables from its input, one `NAME=base64` a line
until an empty one, so no value is on a command line another process
lists; makes a home named from the machine's root absolute; writes the
command's own pid where the end of its tree finds it; then becomes the
command, in its folder, with nothing on its input."""

LIST = r"""
for path in "$1"/* "$1"/.[!.]* "$1"/..?*; do
    [ -e "$path" ] || [ -L "$path" ] || continue
    if [ -d "$path" ] && [ ! -L "$path" ]; then
        echo "d|0|$path"
    else
        echo "f|$(wc -c < "$path" | tr -d ' ')|$path"
    fi
done
"""
"""Each entry of the folder `$1`: whether it is a folder, its size, and its
path."""

WRITE = 'mkdir -p "$(dirname "$1")" && cat > "$1"'


def variables(env: Mapping[str, str]) -> bytes:
    """What the launcher reads: each variable as its name and its value in
    base64, then an empty line."""
    lines = [f"{name}={base64.b64encode(value.encode()).decode()}" for name, value in env.items()]
    return "".join(f"{line}\n" for line in [*lines, ""]).encode()


class TransportVmImpl(TransportInterface):
    """Runs commands in a VM workspace through the channel its machines
    give. A command's environment inside is the guest's login, the
    workspace as its home, a locale, its own variables, and its injected
    secrets, passed on the command's input, never on a command line. The
    command line that reaches the machine carries nothing of the engine's
    environment. At the deadline the command's tree inside the machine ends,
    and the command line with it. Records and the epoch fence are kept on
    this host, beside the workspaces."""

    def __init__(
        self,
        records: Path,
        secrets: SecretsInterface,
        broker: CredentialBrokerInterface,
        machines: MachinesInterface,
        timeout: timedelta,
    ) -> None:
        self._book = RecordBook(records)
        self._secrets = secrets
        self._broker = broker
        self._machines = machines
        self._timeout = timeout

    async def run(
        self,
        workspace: Workspace,
        command: CommandSpec,
        on_output: OutputSink | None = None,
        *,
        seal: RecordSeal,
    ) -> CommandResult:
        name = self._machine(workspace)
        await asyncio.to_thread(self._book.admit, workspace.id, command.epoch)
        folder = _inside(command.cwd)
        if utcnow() >= command.deadline:
            result = CommandResult(key=command.key, exit_code=None, timed_out=True)
        else:
            pidfile = f"/tmp/acme-{command.key.hex}.pid"
            async with injected(self._secrets, self._broker, workspace, command) as injection:
                env = {"HOME": WORKSPACE, "LANG": BASE_LANG, **dict(command.env), **injection.env}
                channel = self._machines.channel(name)
                process = await spawn(
                    launch_argv(channel.argv, pidfile, folder, command.argv),
                    Path("/"),
                    channel.env,
                    stdin=asyncio.subprocess.PIPE,
                )
                assert process.stdin is not None
                process.stdin.write(variables(env))
                try:
                    await process.stdin.drain()
                except BrokenPipeError, ConnectionResetError:
                    pass  # the command line ended first; its exit says why
                process.stdin.close()

                async def end_inside() -> None:
                    await self._machines.run(
                        name, ("sh", "-c", END_TREE, "sh", pidfile), bound=self._timeout
                    )

                async def end() -> None:
                    # The command line first, so nothing more comes through
                    # it, then the command's tree inside, by its pid.
                    await end_tree(process.pid)
                    await end_inside()

                driven = await drive(
                    process,
                    injection.redactor,
                    max_output=command.max_output,
                    deadline=command.deadline,
                    on_output=on_output,
                    end=end,
                    end_left=end_inside,
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
        self._machine(workspace)
        await asyncio.to_thread(self._book.admit, workspace.id, epoch)
        record = await asyncio.to_thread(self._book.read, workspace.id, key)
        return None if record is None else await opened_result(record, seal)

    async def purge_records(self, workspace_id: UUID) -> None:
        await asyncio.to_thread(self._book.purge, workspace_id)

    async def read_file(
        self, workspace: Workspace, path: str, max_bytes: int, offset: int = 0
    ) -> bytes:
        name = self._machine(workspace)
        start = file_offset(offset) + 1
        reply = await self._machines.run(
            name,
            ("sh", "-c", READ_FROM, "sh", _inside(path), str(start), str(max_bytes)),
            bound=self._timeout,
        )
        if not reply.ok:
            raise InfraNotFound(f"no file {path!r} in the workspace: {reply.reason()}")
        return reply.stdout

    async def write_file(self, workspace: Workspace, path: str, data: bytes, epoch: int) -> None:
        name = self._machine(workspace)
        await asyncio.to_thread(self._book.admit, workspace.id, epoch)
        reply = await self._machines.run(
            name, ("sh", "-c", WRITE, "sh", _inside(path)), stdin=data, bound=self._timeout
        )
        if not reply.ok:
            raise BackendFailed("machine", "write", reply.reason())

    async def list_files(self, workspace: Workspace, path: str, limit: int) -> list[FileEntry]:
        name = self._machine(workspace)
        folder = _inside(path)
        reply = await self._machines.run(
            name, ("sh", "-c", f'[ -d "$1" ] || exit 1\n{LIST}', "sh", folder), bound=self._timeout
        )
        if not reply.ok:
            raise InfraNotFound(f"no directory {path!r} in the workspace: {reply.reason()}")
        entries: list[FileEntry] = []
        for line in reply.stdout.decode(errors="replace").splitlines():
            kind, size, full = line.split("|", 2)
            is_dir = kind == "d"
            entries.append(
                FileEntry(
                    path=full.removeprefix(f"{WORKSPACE}/"),
                    is_dir=is_dir,
                    size=0 if is_dir else int(size or 0),
                )
            )
        return sorted(entries, key=lambda entry: entry.path)[:limit]

    def describe(self) -> str:
        return "transport=vm"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def _machine(self, workspace: Workspace) -> str:
        require_mode(workspace, IsolationMode.VM, "vm")
        return workspace.location


def launch_argv(
    channel: Sequence[str], pidfile: str, folder: str, argv: Sequence[str]
) -> list[str]:
    """The command line that runs a command in the machine, through the
    launcher."""
    return [*channel, "sh", "-c", LAUNCHER, "sh", pidfile, folder, *argv]


def _inside(path: str) -> str:
    """The place `path` names under the workspace's folder, from the
    machine's root. Inside the machine a link leads nowhere but the machine,
    so the path alone is held."""
    relative = relative_path(path)
    return WORKSPACE if str(relative) == "." else f"{WORKSPACE}/{relative}"
