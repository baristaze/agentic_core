import asyncio
import hashlib
import os
import shutil
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from acme.infra.exceptions import InfraValidationFailed
from acme.infra.machines import (
    Channel,
    MachineReply,
    MachinesInterface,
    MachineSpec,
    MachineState,
    SnapshotNotFound,
)


@dataclass
class TwinMachine:
    state: MachineState
    spec: MachineSpec
    folder: Path


class MachinesTwinImpl(MachinesInterface):
    """The machines' twin, for tests: a machine is a folder under `root`, its
    root, and its commands are processes of this host run from it, with
    nothing of this process's environment but its path. A snapshot is a
    copy of a stopped machine's folder, held to a digest of what its files
    hold. What a test asks of a backend is set up front: why it runs no
    machine (`hypervisor`), whether it closes egress, the limits it holds,
    and whether its store is encrypted. `calls` keeps each call that would
    make, start, stop, or remove a machine, so a test sees none was made."""

    def __init__(
        self,
        root: Path,
        *,
        hypervisor: str | None = None,
        closes_egress: bool = True,
        limits: frozenset[str] = frozenset({"cpus", "memory_mb"}),
        encrypted: bool = True,
    ) -> None:
        self.root = root
        self.hypervisor = hypervisor
        self._closes_egress = closes_egress
        self._limits = limits
        self._encrypted = encrypted
        self.machines: dict[str, TwinMachine] = {}
        self.calls: list[tuple[str, ...]] = []

    async def probe(self) -> str | None:
        self.calls.append(("probe",))
        return self.hypervisor

    def closes_egress(self) -> bool:
        return self._closes_egress

    def limits(self) -> frozenset[str]:
        return self._limits

    def encrypted_at_rest(self) -> bool:
        return self._encrypted

    async def state(self, name: str) -> MachineState:
        machine = self.machines.get(name)
        return MachineState.ABSENT if machine is None else machine.state

    async def launch(self, name: str, spec: MachineSpec, snapshot: str | None = None) -> None:
        self.calls.append(("launch", name) + (() if snapshot is None else (snapshot,)))
        if self.hypervisor is not None:
            raise InfraValidationFailed(f"the twin runs no machine: {self.hypervisor}")
        if not spec.egress_open and not self._closes_egress:
            raise InfraValidationFailed("the twin cannot close a machine's egress")
        machine = self.machines.get(name)
        if machine is None:
            folder = self.root / name
            if snapshot is not None:
                source = self.machines.get(snapshot)
                if source is None or source.state is not MachineState.STOPPED:
                    raise SnapshotNotFound(f"no snapshot stands under {snapshot}")
                await asyncio.to_thread(shutil.copytree, source.folder, folder, symlinks=True)
            else:
                folder.mkdir(parents=True)
            machine = self.machines[name] = TwinMachine(MachineState.RUNNING, spec, folder)
        machine.state = MachineState.RUNNING
        machine.spec = spec

    async def run(
        self,
        name: str,
        argv: Sequence[str],
        *,
        bound: timedelta,
        stdin: bytes | None = None,
    ) -> MachineReply:
        machine = self.machines.get(name)
        if machine is None or machine.state is not MachineState.RUNNING:
            return MachineReply(255, b"", f"no machine runs under {name}".encode())
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=machine.folder,
            env=self.channel(name).env,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(stdin), bound.total_seconds()
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            return MachineReply(None, b"", b"")
        return MachineReply(process.returncode, stdout, stderr)

    def channel(self, name: str) -> Channel:
        folder = self.root / name
        # A machine that does not run has a folder no command finds.
        running = name in self.machines and self.machines[name].state is MachineState.RUNNING
        target = str(folder) if running else str(folder / ".stopped")
        return Channel(
            argv=("sh", "-c", 'cd "$0" || exit 255; exec "$@"', target),
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        )

    async def stop(self, name: str) -> None:
        self.calls.append(("stop", name))
        machine = self.machines.get(name)
        if machine is not None:
            machine.state = MachineState.STOPPED

    async def snapshot(self, name: str, to: str) -> str:
        self.calls.append(("snapshot", name, to))
        if to not in self.machines:
            source = self.machines.get(name)
            if source is None or source.state is not MachineState.STOPPED:
                raise SnapshotNotFound(f"nothing stopped stands under {name}")
            folder = self.root / to
            await asyncio.to_thread(shutil.copytree, source.folder, folder, symlinks=True)
            self.machines[to] = TwinMachine(MachineState.STOPPED, source.spec, folder)
        digest = await self.digest(to)
        assert digest is not None
        return digest

    async def digest(self, snapshot: str) -> str | None:
        machine = self.machines.get(snapshot)
        if machine is None:
            return None
        digest = hashlib.sha256()
        for path in sorted(machine.folder.rglob("*")):
            digest.update(str(path.relative_to(machine.folder)).encode() + b"\0")
            if path.is_file() and not path.is_symlink():
                digest.update(path.read_bytes())
        return "sha256:" + digest.hexdigest()

    async def read(self, snapshot: str) -> AsyncIterator[bytes]:
        machine = self.machines.get(snapshot)
        if machine is None or machine.state is not MachineState.STOPPED:
            raise SnapshotNotFound(f"no snapshot stands under {snapshot}")
        for path in sorted(machine.folder.rglob("*")):
            if path.is_file() and not path.is_symlink():
                yield path.read_bytes()

    async def names(self, prefix: str) -> list[str]:
        return sorted(name for name in self.machines if name.startswith(prefix))

    async def destroy(self, name: str) -> None:
        self.calls.append(("destroy", name))
        machine = self.machines.pop(name, None)
        if machine is not None:
            await asyncio.to_thread(shutil.rmtree, machine.folder, ignore_errors=True)

    def describe(self) -> str:
        return "machines=twin"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None


class MachinesNullImpl(MachinesInterface):
    """A process that runs no machine: its probe says so, so every VM
    workspace is refused, loudly, before anything is made."""

    async def probe(self) -> str | None:
        return "this process runs no machines"

    def closes_egress(self) -> bool:
        return False

    def limits(self) -> frozenset[str]:
        return frozenset()

    def encrypted_at_rest(self) -> bool:
        return False

    async def state(self, name: str) -> MachineState:
        return MachineState.ABSENT

    async def launch(self, name: str, spec: MachineSpec, snapshot: str | None = None) -> None:
        raise InfraValidationFailed("this process runs no machines")

    async def run(
        self,
        name: str,
        argv: Sequence[str],
        *,
        bound: timedelta,
        stdin: bytes | None = None,
    ) -> MachineReply:
        return MachineReply(255, b"", b"this process runs no machines")

    def channel(self, name: str) -> Channel:
        return Channel(argv=("false",), env={})

    async def stop(self, name: str) -> None:
        return None

    async def snapshot(self, name: str, to: str) -> str:
        raise SnapshotNotFound("this process runs no machines")

    async def digest(self, snapshot: str) -> str | None:
        return None

    async def read(self, snapshot: str) -> AsyncIterator[bytes]:
        raise SnapshotNotFound("this process runs no machines")
        yield b""  # an async generator, as the interface reads

    async def names(self, prefix: str) -> list[str]:
        return []

    async def destroy(self, name: str) -> None:
        return None

    def describe(self) -> str:
        return "machines=none"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None
