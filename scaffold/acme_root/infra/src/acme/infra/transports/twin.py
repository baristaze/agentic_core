import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from uuid import UUID

from acme.infra.base import utcnow
from acme.infra.exceptions import InfraNotFound
from acme.infra.secrets import SecretsInterface
from acme.infra.transports import (
    CapabilityMissing,
    CommandResult,
    CommandSpec,
    CredentialBrokerInterface,
    FileEntry,
    OutputSink,
    StaleCommand,
    TransportInterface,
    relative_path,
    require_mode,
)
from acme.infra.transports.injection import injected
from acme.infra.workspaces import IsolationMode, Workspace


@dataclass(frozen=True)
class TwinReply:
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""


TwinHandler = Callable[[CommandSpec, Mapping[str, str]], Awaitable[TwinReply]]
"""Answers a command, given the command and the environment it would get,
injected secrets included."""


async def _quiet(command: CommandSpec, env: Mapping[str, str]) -> TwinReply:
    return TwinReply()


class TransportTwinImpl(TransportInterface):
    """The transport's twin, over the twin's workspaces: files in memory, and
    each command answered by a handler a test sets, with no process. It
    fences, injects, redacts, streams, and records as the real transports
    do, so what sits above it runs the same way twice. A handler that has
    not answered by the deadline is cancelled, and the command timed out."""

    def __init__(
        self,
        secrets: SecretsInterface,
        broker: CredentialBrokerInterface,
        handler: TwinHandler = _quiet,
    ) -> None:
        self._secrets = secrets
        self._broker = broker
        self.handler = handler
        self._files: dict[UUID, dict[str, bytes]] = {}
        self._records: dict[tuple[UUID, UUID], CommandResult] = {}
        self._epochs: dict[UUID, int] = {}
        self.commands: list[CommandSpec] = []

    async def run(
        self, workspace: Workspace, command: CommandSpec, on_output: OutputSink | None = None
    ) -> CommandResult:
        self._serve(workspace)
        self._admit(workspace.id, command.epoch)
        self.commands.append(command)
        result = CommandResult(key=command.key, exit_code=None, timed_out=True)
        left = (command.deadline - utcnow()).total_seconds()
        if left > 0:
            async with injected(self._secrets, self._broker, workspace, command) as injection:
                env = {**dict(command.env), **injection.env}
                try:
                    reply = await asyncio.wait_for(self.handler(command, env), left)
                except TimeoutError:
                    reply = None
                if reply is not None:
                    stdout = injection.redactor.redact(reply.stdout)
                    stderr = injection.redactor.redact(reply.stderr)
                    for stream, text in (("stdout", stdout), ("stderr", stderr)):
                        if text and on_output is not None:
                            await on_output(stream, text)
                    result = CommandResult(
                        key=command.key,
                        exit_code=reply.exit_code,
                        stdout=stdout[: command.max_output],
                        stderr=stderr[: command.max_output],
                        truncated=max(len(stdout), len(stderr)) > command.max_output,
                        secrets=injection.names,
                    )
                else:
                    result = result.model_copy(update={"secrets": injection.names})
        self._records[(workspace.id, command.key)] = result
        return result

    async def outcome(self, workspace: Workspace, key: UUID, epoch: int) -> CommandResult | None:
        self._serve(workspace)
        self._admit(workspace.id, epoch)
        return self._records.get((workspace.id, key))

    async def read_file(self, workspace: Workspace, path: str, max_bytes: int) -> bytes:
        self._serve(workspace)
        files = self._files.get(workspace.id, {})
        name = str(relative_path(path))
        if name not in files:
            raise InfraNotFound(f"no file {path!r} in the workspace")
        return files[name][:max_bytes]

    async def write_file(self, workspace: Workspace, path: str, data: bytes, epoch: int) -> None:
        self._serve(workspace)
        self._admit(workspace.id, epoch)
        self._files.setdefault(workspace.id, {})[str(relative_path(path))] = data

    async def list_files(self, workspace: Workspace, path: str, limit: int) -> list[FileEntry]:
        self._serve(workspace)
        folder = relative_path(path)
        files = self._files.get(workspace.id, {})
        prefix = "" if str(folder) == "." else f"{folder}/"
        found = sorted(
            name for name in files if name.startswith(prefix) and "/" not in name[len(prefix) :]
        )
        return [FileEntry(path=name, is_dir=False, size=len(files[name])) for name in found][:limit]

    def describe(self) -> str:
        return "transport=twin"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def _serve(self, workspace: Workspace) -> None:
        require_mode(workspace, IsolationMode.TWIN, "twin")

    def _admit(self, workspace_id: UUID, epoch: int) -> None:
        seen = self._epochs.get(workspace_id, 0)
        if epoch < seen:
            raise StaleCommand(
                f"workspace {workspace_id} runs commands at epoch {seen}; {epoch} is stale"
            )
        self._epochs[workspace_id] = epoch


class TransportNullImpl(TransportInterface):
    """A process with no transport: every call is refused, loudly, with the
    typed error the model reads. Never a quiet success."""

    async def run(
        self, workspace: Workspace, command: CommandSpec, on_output: OutputSink | None = None
    ) -> CommandResult:
        raise CapabilityMissing("this agent has no workspace")

    async def outcome(self, workspace: Workspace, key: UUID, epoch: int) -> CommandResult | None:
        raise CapabilityMissing("this agent has no workspace")

    async def read_file(self, workspace: Workspace, path: str, max_bytes: int) -> bytes:
        raise CapabilityMissing("this agent has no workspace")

    async def write_file(self, workspace: Workspace, path: str, data: bytes, epoch: int) -> None:
        raise CapabilityMissing("this agent has no workspace")

    async def list_files(self, workspace: Workspace, path: str, limit: int) -> list[FileEntry]:
        raise CapabilityMissing("this agent has no workspace")

    def describe(self) -> str:
        return "transport=none"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None
