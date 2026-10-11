"""Machines: virtual machines a workspace runs on, a capability, not a
domain. A backend starts a machine from an image or from a snapshot, runs a
command in it, stops it, snapshots its disk, and destroys it. Each machine
and each snapshot has a name its caller chooses; a backend keeps them apart
by that name alone.

A backend says up front what it can hold: whether this host runs a machine
at all, under names as long as its caller's (its `probe` of the hypervisor
and of what a name may be), the egress it enforces outside the
guest, the limits it enforces, and whether the store that keeps its
snapshots is encrypted at rest. A caller refuses what a backend cannot hold
before a machine starts, and never asks for less in its stead.

A snapshot is a stopped machine's disk, kept in the backend's own store
under a name, and held to a digest: a disk is gigabytes, so it never passes
through memory or a bucket whole. A snapshot starts machines, makes further
snapshots, and is read back as a stream (`read`), so what it holds can be
scanned. A machine and a snapshot are destroyed alike, by name."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from pydantic import Field

from acme.infra.base import InfraModel
from acme.infra.exceptions import InfraNotFound

__all__ = [
    "Channel",
    "MachineReply",
    "MachineSpec",
    "MachineState",
    "MachinesInterface",
    "SnapshotNotFound",
]


class MachineState(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"  # its disk kept, nothing running
    ABSENT = "absent"  # no machine, and no snapshot, under the name


class MachineSpec(InfraModel):
    """What a machine is started to: the image a new one boots from, in the
    backend's own terms, the whole cpus and the memory it may use (None asks
    for the backend's default), and whether anything leaves it."""

    image: str = Field(min_length=1)
    cpus: int | None = Field(default=None, gt=0)
    memory_mb: int | None = Field(default=None, gt=0)
    egress_open: bool


@dataclass(frozen=True)
class MachineReply:
    code: int | None  # None when it did not answer in time
    stdout: bytes
    stderr: bytes

    @property
    def ok(self) -> bool:
        return self.code == 0

    def reason(self) -> str:
        if self.code is None:
            return "the machine did not answer in time"
        lines = self.stderr.decode(errors="replace").strip().splitlines()
        return lines[-1] if lines else f"the command exited {self.code}"


@dataclass(frozen=True)
class Channel:
    """The command line on the engine's host that runs a command in a
    machine: `argv`, then the command's own arguments, run with `env` alone
    for an environment. The command starts in the machine's root, reads the
    command line's standard input, and its exit is the command line's.
    `env` holds what the command line needs to reach the machine and
    nothing of the engine's credentials, and nothing of it enters the
    machine."""

    argv: tuple[str, ...]
    env: Mapping[str, str]


class SnapshotNotFound(InfraNotFound):
    """No snapshot stands under the name asked for."""

    code = "snapshot_not_found"


class MachinesInterface(ABC):
    @abstractmethod
    async def probe(self, longest: str) -> str | None:
        """Why this host runs no machine, such as a hypervisor it lacks, a
        backend it cannot reach, or a name as long as `longest`, the longest
        its caller gives, that it cannot keep; None when it runs one."""
        ...

    @abstractmethod
    def closes_egress(self) -> bool:
        """Whether this backend closes a machine's egress from outside its
        guest. One that cannot starts every machine reaching out, so a
        machine asked to reach nothing is refused before it starts."""
        ...

    @abstractmethod
    def limits(self) -> frozenset[str]:
        """The limits of a `MachineSpec` this backend enforces, by name."""
        ...

    @abstractmethod
    def encrypted_at_rest(self) -> bool:
        """Whether the store that keeps this backend's snapshots encrypts
        them at rest."""
        ...

    @abstractmethod
    async def state(self, name: str) -> MachineState: ...

    @abstractmethod
    async def launch(self, name: str, spec: MachineSpec, snapshot: str | None = None) -> None:
        """The machine under `name` running to `spec`. One that does not
        stand is made: from `snapshot`, a copy of its disk, or from
        `spec.image`. One that stands keeps its disk, and is started again
        to `spec` when it was stopped or started to another. A launch that
        fails part way leaves no machine it made."""
        ...

    @abstractmethod
    async def run(
        self,
        name: str,
        argv: Sequence[str],
        *,
        bound: timedelta,
        stdin: bytes | None = None,
    ) -> MachineReply:
        """Runs `argv` in the running machine under `name`, from its root,
        and answers its exit and output; one that has not ended within
        `bound` is ended and answers no code."""
        ...

    @abstractmethod
    def channel(self, name: str) -> Channel:
        """How a command that streams, such as a transport's, reaches the
        machine under `name`."""
        ...

    @abstractmethod
    async def stop(self, name: str) -> None:
        """The machine under `name` stopped, its disk kept. Stopping one that
        is stopped, or absent, does nothing."""
        ...

    @abstractmethod
    async def snapshot(self, name: str, to: str) -> str:
        """The disk of the stopped machine, or of the snapshot, under `name`,
        kept as the snapshot `to`, and its digest. One that stands under `to`
        already is kept as it is. `SnapshotNotFound` when nothing stopped
        stands under `name`."""
        ...

    @abstractmethod
    async def digest(self, snapshot: str) -> str | None:
        """The digest of the disk the snapshot under `snapshot` holds now;
        None when none stands under it."""
        ...

    @abstractmethod
    def read(self, snapshot: str) -> AsyncIterator[bytes]:
        """What the snapshot's disk holds, in parts, in its order.
        `SnapshotNotFound` when none stands under it."""
        ...

    @abstractmethod
    async def names(self, prefix: str) -> list[str]:
        """Every machine and snapshot whose name starts with `prefix`."""
        ...

    @abstractmethod
    async def destroy(self, name: str) -> None:
        """The machine, or the snapshot, under `name` gone, its disk with it.
        Destroying one already gone does nothing; one this backend cannot
        remove is an error."""
        ...

    @abstractmethod
    def describe(self) -> str: ...

    @abstractmethod
    async def start(self) -> None:
        """Opened at boot. An impl that holds no connection of its own
        returns None."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Closed at shutdown."""
        ...
