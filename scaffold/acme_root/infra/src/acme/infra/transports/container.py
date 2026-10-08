import asyncio
from collections.abc import Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from acme.infra.base import utcnow
from acme.infra.docker import docker, docker_environment
from acme.infra.exceptions import BackendFailed, InfraNotFound
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
from acme.infra.transports.injection import BASE_LANG, injected
from acme.infra.transports.processes import drive, end_tree, spawn
from acme.infra.transports.records import RecordBook, opened_result, sealed_record
from acme.infra.workspaces import IsolationMode, Workspace
from acme.infra.workspaces.container import MOUNT

LAUNCHER = 'echo $$ > "$1"; shift; exec "$@"'
"""Writes the command's own pid where the end of its tree finds it, then
becomes the command."""

READ_FROM = (
    'test -f "$1" || { echo "no file $1" >&2; exit 1; }; tail -c "+$2" -- "$1" | head -c "$3"'
)
"""At most `$3` bytes of the file `$1` from its byte `$2`, counted from one:
`tail` seeks a regular file to it, so no byte before it is read."""

END_TREE = r"""
root=$(cat "$1" 2>/dev/null) || exit 0
kids() {
    for status in /proc/[0-9]*/status; do
        parent=$(sed -n 's/^PPid:[[:space:]]*//p' "$status" 2>/dev/null)
        if [ "$parent" = "$1" ]; then pid=${status#/proc/}; echo "${pid%/status}"; fi
    done
}
tree() { echo "$1"; for kid in $(kids "$1"); do tree "$kid"; done; }
for _ in 1 2 3; do kill -STOP $(tree "$root") 2>/dev/null; done
kill -KILL $(tree "$root") 2>/dev/null
rm -f "$1"
true
"""
"""Ends a command's tree inside its container, by the pid its launcher
wrote: the tree is frozen, walked again, and killed, as on a host."""


def exec_argv(
    container: str,
    workdir: str,
    plain: Mapping[str, str],
    secrets: Sequence[str],
    pidfile: str,
    argv: Sequence[str],
) -> list[str]:
    """The `docker exec` that runs a command. A plain variable goes by name
    and value; a secret by name alone, its value in the command line's own
    environment, so no other process lists it. None of the command's
    variables becomes the command line's own: a tool's `HOME` or `PATH`
    never picks which Docker it reaches, or whether it finds one."""
    variables = [flag for item in plain.items() for flag in ("--env", "=".join(item))]
    variables += [flag for secret in secrets for flag in ("--env", secret)]
    return [
        "docker",
        "exec",
        "--workdir",
        workdir,
        *variables,
        container,
        "sh",
        "-c",
        LAUNCHER,
        "sh",
        pidfile,
        *argv,
    ]


class TransportContainerImpl(TransportInterface):
    """Runs commands in a container workspace through `docker exec`. A
    command's environment inside is the image's, the workspace as its home,
    a locale, its own variables, and its injected secrets
    (`exec_argv`). At the deadline the command's tree inside the container
    ends, and the command line with it. Records and the epoch fence are
    kept on this host, beside the workspaces."""

    def __init__(
        self,
        records: Path,
        secrets: SecretsInterface,
        broker: CredentialBrokerInterface,
        timeout: timedelta,
    ) -> None:
        self._book = RecordBook(records)
        self._secrets = secrets
        self._broker = broker
        self._timeout = timeout

    async def run(
        self,
        workspace: Workspace,
        command: CommandSpec,
        on_output: OutputSink | None = None,
        *,
        seal: RecordSeal,
    ) -> CommandResult:
        name = self._container(workspace)
        await asyncio.to_thread(self._book.admit, workspace.id, command.epoch)
        workdir = _inside(command.cwd)
        if utcnow() >= command.deadline:
            result = CommandResult(key=command.key, exit_code=None, timed_out=True)
        else:
            pidfile = f"/tmp/acme-{command.key.hex}.pid"
            async with injected(self._secrets, self._broker, workspace, command) as injection:
                plain = {"HOME": MOUNT, "LANG": BASE_LANG, **dict(command.env)}
                process = await spawn(
                    exec_argv(name, workdir, plain, tuple(injection.env), pidfile, command.argv),
                    Path("/"),
                    docker_environment(injection.env),
                )

                async def end_inside() -> None:
                    await docker(
                        "exec", name, "sh", "-c", END_TREE, "sh", pidfile, bound=self._timeout
                    )

                async def end() -> None:
                    await end_inside()
                    await end_tree(process.pid)

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
        self._container(workspace)
        await asyncio.to_thread(self._book.admit, workspace.id, epoch)
        record = await asyncio.to_thread(self._book.read, workspace.id, key)
        return None if record is None else await opened_result(record, seal)

    async def purge_records(self, workspace_id: UUID) -> None:
        await asyncio.to_thread(self._book.purge, workspace_id)

    async def read_file(
        self, workspace: Workspace, path: str, max_bytes: int, offset: int = 0
    ) -> bytes:
        name = self._container(workspace)
        target = _inside(path)
        start = file_offset(offset) + 1
        reply = await docker(
            "exec",
            name,
            "sh",
            "-c",
            READ_FROM,
            "sh",
            target,
            str(start),
            str(max_bytes),
            bound=self._timeout,
        )
        if not reply.ok:
            raise InfraNotFound(f"no file {path!r} in the workspace: {reply.reason()}")
        return reply.stdout

    async def write_file(self, workspace: Workspace, path: str, data: bytes, epoch: int) -> None:
        name = self._container(workspace)
        await asyncio.to_thread(self._book.admit, workspace.id, epoch)
        target = _inside(path)
        reply = await docker(
            "exec",
            "--interactive",
            name,
            "sh",
            "-c",
            'mkdir -p "$(dirname "$1")" && cat > "$1"',
            "sh",
            target,
            stdin=data,
            bound=self._timeout,
        )
        if not reply.ok:
            raise BackendFailed("docker", "exec write", reply.reason())

    async def list_files(self, workspace: Workspace, path: str, limit: int) -> list[FileEntry]:
        name = self._container(workspace)
        folder = _inside(path)
        reply = await docker(
            "exec",
            name,
            "find",
            folder,
            "-mindepth",
            "1",
            "-maxdepth",
            "1",
            "-exec",
            "stat",
            "-c",
            "%F|%s|%n",
            "{}",
            "+",
            bound=self._timeout,
        )
        if not reply.ok:
            raise InfraNotFound(f"no directory {path!r} in the workspace: {reply.reason()}")
        entries: list[FileEntry] = []
        for line in sorted(reply.stdout.decode(errors="replace").splitlines()):
            kind, size, full = line.split("|", 2)
            is_dir = kind == "directory"
            entries.append(
                FileEntry(
                    path=full.removeprefix(f"{MOUNT}/"),
                    is_dir=is_dir,
                    size=0 if is_dir else int(size),
                )
            )
        return sorted(entries, key=lambda entry: entry.path)[:limit]

    def describe(self) -> str:
        return "transport=container"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def _container(self, workspace: Workspace) -> str:
        require_mode(workspace, IsolationMode.CONTAINER, "container")
        return workspace.location


def _inside(path: str) -> str:
    """The place `path` names under the workspace's mount. Inside the
    container a link leads nowhere but the container, so the path alone is
    held."""
    relative = relative_path(path)
    return MOUNT if str(relative) == "." else f"{MOUNT}/{relative}"
