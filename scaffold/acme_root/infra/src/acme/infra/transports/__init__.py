"""Transports: the one way a command runs in a workspace, and a file there is
read, written, or listed, wherever the workspace is: a directory on this
host, a container, or a twin. Every tool that runs a command or touches a
file goes through a transport, and none reaches past it.

A command carries three things from the run that sends it. Its key, the id
of its tool request, under which the transport records how it ended, so a
new run can ask after a crash. Its writer epoch, which the transport fences
on its own side: a command from a run that lost its claim is refused
(`StaleCommand`). And its deadline, when the command's whole process tree
ends, children and all.

The record keeps what recovery decides by in the clear: the exit, whether
the command timed out or was cut, and the secrets it used, by name. Its
output is content, so it is kept sealed under the key of the session the
command runs for, by the seal the engine hands over with the command
(`RecordSeal`); the transport never holds that key. A workspace's records
go when its session is purged (`purge_records`).

A secret reaches a command by name, never by value (ADR 1003). A brokered
one is attached outside the workspace by the credential broker, per
destination, and the process never holds it. Only when a secret must enter
the process is it injected: resolved for this one command by the transport,
into an environment built from nothing, so no credential of the engine's
reaches it, and redacted from everything the command prints, raw, encoded,
or escaped, before anything streams or returns. The value is dropped when
the command ends; the result names each secret it used, never a value."""

import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from acme.infra.base import InfraModel
from acme.infra.exceptions import InfraException, InfraValidationFailed
from acme.infra.transports.redaction import Redactor
from acme.infra.workspaces import IsolationMode, Workspace

__all__ = [
    "CapabilityMissing",
    "CommandResult",
    "CommandSpec",
    "CredentialBrokerInterface",
    "FileEntry",
    "OutputSink",
    "PathOutsideWorkspace",
    "RecordSeal",
    "SecretUse",
    "SecretVia",
    "StaleCommand",
    "TransportInterface",
    "file_offset",
    "relative_path",
    "require_mode",
]

ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")


class CapabilityMissing(InfraException):
    """A capability this agent does not have, such as a workspace: refused
    loudly, never answered with a quiet success."""

    http_status = 501
    code = "capability_missing"


class StaleCommand(InfraException):
    """A command from a run whose writer epoch another run has passed: the
    run lost its claim, and nothing it sends runs."""

    http_status = 412
    code = "stale_command"


class PathOutsideWorkspace(InfraValidationFailed):
    code = "path_outside_workspace"


class SecretVia(StrEnum):
    BROKERED = "brokered"  # attached outside the workspace; the process never holds it
    INJECTED = "injected"  # placed in the one process's environment


class SecretUse(InfraModel):
    """A secret a command needs, by name: brokered to a destination, or
    injected into one variable of the process's environment."""

    name: str = Field(min_length=1, max_length=200, pattern=r"^[^/]+$")
    via: SecretVia
    env: str | None = None
    destination: str | None = None

    @model_validator(mode="after")
    def _names_where_it_goes(self) -> Self:
        if self.via is SecretVia.INJECTED and (self.env is None or not ENV_NAME.match(self.env)):
            raise ValueError("an injected secret names the variable it lands in")
        if self.via is SecretVia.BROKERED and not self.destination:
            raise ValueError("a brokered secret names the destination it is attached for")
        return self


class CommandSpec(InfraModel):
    argv: tuple[str, ...] = Field(min_length=1)
    cwd: str = "."  # inside the workspace
    env: tuple[tuple[str, str], ...] = ()  # never a secret: those go by name, in `secrets`
    secrets: tuple[SecretUse, ...] = ()
    key: UUID  # the id of the tool request it serves
    epoch: int = Field(ge=0)  # the writer epoch of the run that sends it
    deadline: datetime  # when its whole process tree ends
    # The characters kept of each stream; past them, its head and its tail.
    max_output: int = Field(default=1_000_000, gt=0)

    @model_validator(mode="after")
    def _one_name_one_variable(self) -> Self:
        names = [name for name, _ in self.env]
        names += [use.env for use in self.secrets if use.env is not None]
        if len(set(names)) != len(names) or not all(ENV_NAME.match(name) for name in names):
            raise ValueError("each variable is named once, in capitals")
        if len({use.name for use in self.secrets}) != len(self.secrets):
            raise ValueError("a command names each secret once")
        return self


class CommandResult(InfraModel):
    """How a command ended. Its output is redacted already. A command that
    ran out of time has no exit code; `secrets` names what it used."""

    key: UUID
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    truncated: bool = False  # an output past the command's bound was cut in its middle
    secrets: tuple[str, ...] = ()


class FileEntry(InfraModel):
    path: str  # relative to the workspace
    is_dir: bool
    size: int = Field(ge=0)


OutputSink = Callable[[str, str], Awaitable[None]]
"""Called with the stream (`stdout` or `stderr`) and redacted text as it
arrives."""

Sealing = Callable[[bytes], Awaitable[bytes | None]]
"""Seals one command's output, or opens it: bytes in, bytes or None out."""


@dataclass(frozen=True)
class RecordSeal:
    """How one command's output is sealed in its record, and opened again,
    under the key of the session the command runs for: handed over by the
    engine that sends the command, bound to it, so the transport never
    holds the key. `seal` answers None when the session keeps no content at
    rest, memory-only or revoked, and the record keeps the outcome without
    its output. `open` answers None once the key the output was sealed
    under is destroyed, and refuses a blob sealed for anything else."""

    seal: Sealing
    open: Sealing


def relative_path(path: str) -> PurePosixPath:
    """A path inside a workspace, refused when it is absolute or climbs out."""
    relative = PurePosixPath(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise PathOutsideWorkspace(f"{path!r} is not a path inside the workspace")
    return relative


def file_offset(offset: int) -> int:
    """An offset into a file, refused when it is before the file's start."""
    if offset < 0:
        raise InfraValidationFailed(f"an offset of {offset} is before the start of the file")
    return offset


def require_mode(workspace: Workspace, mode: IsolationMode, transport: str) -> None:
    """Refuses, loudly, a workspace the transport does not serve: the absent
    workspace of a session that has none, or one of another mode."""
    if workspace.spec.mode is IsolationMode.NONE:
        raise CapabilityMissing("this agent has no workspace")
    if workspace.spec.mode is not mode:
        raise CapabilityMissing(
            f"the {transport} transport does not run in a {workspace.spec.mode.value} workspace"
        )


class TransportInterface(ABC):
    """Every operation refuses a workspace of a mode this transport does not
    serve, the absent workspace of a session that has none among them, with
    `CapabilityMissing`."""

    @abstractmethod
    async def run(
        self,
        workspace: Workspace,
        command: CommandSpec,
        on_output: OutputSink | None = None,
        *,
        seal: RecordSeal,
    ) -> CommandResult:
        """Runs the command, streaming its redacted output to `on_output`,
        and records how it ended under its key, its output sealed by `seal`.
        A non-zero exit is a result. The command is over when its own
        process exits: what it left holding its output then ends, after a
        short drain. At the deadline the command's whole process tree ends,
        and the result says it timed out; a run that is cancelled ends the
        tree too. `StaleCommand` for an epoch below one
        this workspace has seen, with nothing run."""
        ...

    @abstractmethod
    async def outcome(
        self, workspace: Workspace, key: UUID, epoch: int, *, seal: RecordSeal
    ) -> CommandResult | None:
        """How the command under `key` ended, as recorded, its output opened
        by `seal`: empty when the record kept none, or the key it was sealed
        under is gone. None when it has no record: it never ran, or it was
        cut off before it ended. It admits `epoch` first, as a command does,
        so once a new run has asked, no command from the run it replaced runs
        here (`StaleCommand` for a stale `epoch`)."""
        ...

    @abstractmethod
    async def purge_records(self, workspace_id: UUID) -> None:
        """Removes what the transport keeps of the workspace beside it: how
        each of its commands ended, and the epoch it fences at. A workspace
        with none does nothing."""
        ...

    @abstractmethod
    async def read_file(
        self, workspace: Workspace, path: str, max_bytes: int, offset: int = 0
    ) -> bytes:
        """At most `max_bytes` of the file from `offset`, with no byte before
        it read, so a caller that follows a growing file reads only what is
        new; empty at or past its end. `InfraValidationFailed` for a
        negative offset."""
        ...

    @abstractmethod
    async def write_file(self, workspace: Workspace, path: str, data: bytes, epoch: int) -> None:
        """Writes the file whole, fenced like a command."""
        ...

    @abstractmethod
    async def list_files(self, workspace: Workspace, path: str, limit: int) -> list[FileEntry]:
        """The entries of a directory, by path, at most `limit` of them."""
        ...

    @abstractmethod
    def describe(self) -> str: ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...


class CredentialBrokerInterface(ABC):
    """Attaches a credential outside the workspace, per destination, for one
    command: an egress proxy or a credential helper the agent's process
    reaches and never holds the secret of."""

    @abstractmethod
    async def attach(self, workspace: Workspace, key: UUID, use: SecretUse) -> None:
        """Attaches the secret for the command under `key`; refuses loudly
        when it cannot."""
        ...

    @abstractmethod
    async def detach(self, workspace: Workspace, key: UUID) -> None:
        """Takes back everything attached for the command under `key`."""
        ...

    @abstractmethod
    async def detach_all(self, workspace: Workspace) -> None:
        """Takes back everything attached in the workspace, whatever command
        it was attached for, one whose run was lost before it took it back
        included: what a workspace holds when it is snapshotted is never a
        credential."""
        ...

    @abstractmethod
    def describe(self) -> str: ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...


def secret_held(data: bytes, secrets: Mapping[str, str]) -> str | None:
    """The name of a secret, of `secrets` by name, whose value `data` holds in
    any form redaction matches; None when it holds none. What a workspace's
    snapshot is scanned for before anything of it is kept."""
    text = data.decode("utf-8", errors="surrogateescape")
    for _, _, name in Redactor(secrets).matches(text):
        return name
    return None
