import asyncio
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

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
    RecordSeal,
    StaleCommand,
    TransportInterface,
    relative_path,
    require_mode,
)
from acme.infra.transports.injection import injected
from acme.infra.transports.processes import bound_text
from acme.infra.transports.records import CommandRecord, opened_result, sealed_record
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
    do, its records sealed in `records`, so what sits above it runs the same
    way twice. A handler that has not answered by the deadline is
    cancelled, and the command timed out."""

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
        self.records: dict[tuple[UUID, UUID], CommandRecord] = {}
        self._epochs: dict[UUID, int] = {}
        self.commands: list[CommandSpec] = []

    async def run(
        self,
        workspace: Workspace,
        command: CommandSpec,
        on_output: OutputSink | None = None,
        *,
        seal: RecordSeal,
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
                    kept_out, cut_out = bound_text(stdout, command.max_output)
                    kept_err, cut_err = bound_text(stderr, command.max_output)
                    result = CommandResult(
                        key=command.key,
                        exit_code=reply.exit_code,
                        stdout=kept_out,
                        stderr=kept_err,
                        truncated=cut_out or cut_err,
                        secrets=injection.names,
                    )
                else:
                    result = result.model_copy(update={"secrets": injection.names})
        self.records[(workspace.id, command.key)] = await sealed_record(result, seal)
        return result

    async def outcome(
        self, workspace: Workspace, key: UUID, epoch: int, *, seal: RecordSeal
    ) -> CommandResult | None:
        self._serve(workspace)
        self._admit(workspace.id, epoch)
        record = self.records.get((workspace.id, key))
        return None if record is None else await opened_result(record, seal)

    async def purge_records(self, workspace_id: UUID) -> None:
        for kept in [kept for kept in self.records if kept[0] == workspace_id]:
            del self.records[kept]
        self._epochs.pop(workspace_id, None)

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
        self,
        workspace: Workspace,
        command: CommandSpec,
        on_output: OutputSink | None = None,
        *,
        seal: RecordSeal,
    ) -> CommandResult:
        raise CapabilityMissing("this agent has no workspace")

    async def outcome(
        self, workspace: Workspace, key: UUID, epoch: int, *, seal: RecordSeal
    ) -> CommandResult | None:
        raise CapabilityMissing("this agent has no workspace")

    async def read_file(self, workspace: Workspace, path: str, max_bytes: int) -> bytes:
        raise CapabilityMissing("this agent has no workspace")

    async def write_file(self, workspace: Workspace, path: str, data: bytes, epoch: int) -> None:
        raise CapabilityMissing("this agent has no workspace")

    async def list_files(self, workspace: Workspace, path: str, limit: int) -> list[FileEntry]:
        raise CapabilityMissing("this agent has no workspace")

    async def purge_records(self, workspace_id: UUID) -> None:
        """A process with no transport keeps no record, so it has none to
        remove."""
        return None

    def describe(self) -> str:
        return "transport=none"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None


NONCE_BYTES = 12


class RecordSealTwin:
    """A session's seal as a test holds one: AES-GCM under a key in memory,
    which `revoke` destroys, as revoking a session's key does. `seal` is what
    a command goes with."""

    def __init__(self) -> None:
        self._key: bytes | None = AESGCM.generate_key(bit_length=256)
        self.seal = RecordSeal(seal=self._sealed, open=self._opened)

    def revoke(self) -> None:
        self._key = None

    async def _sealed(self, data: bytes) -> bytes | None:
        if self._key is None:
            return None
        nonce = os.urandom(NONCE_BYTES)
        return nonce + AESGCM(self._key).encrypt(nonce, data, None)

    async def _opened(self, sealed: bytes) -> bytes | None:
        if self._key is None:
            return None
        nonce, body = sealed[:NONCE_BYTES], sealed[NONCE_BYTES:]
        try:
            return AESGCM(self._key).decrypt(nonce, body, None)
        except InvalidTag:
            raise ValueError("the record does not open under this seal") from None
