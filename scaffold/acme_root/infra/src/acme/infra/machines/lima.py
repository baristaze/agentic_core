"""Machines on this host's hypervisor, through Lima's command line
(`limactl`): Apple's virtualization on macOS, KVM on Linux. Each machine is
an instance of Lima, from a template this module writes, and each snapshot
is a stopped instance of its own: its disk a copy-on-write clone of the
machine's, where the filesystem clones files (ADR 1029)."""

import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import timedelta
from pathlib import Path

from acme.infra.exceptions import BackendFailed, InfraValidationFailed
from acme.infra.machines import (
    Channel,
    MachineReply,
    MachinesInterface,
    MachineSpec,
    MachineState,
    SnapshotNotFound,
)

LIMA_VARIABLES = ("PATH", "HOME", "LIMA_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME")
"""What the command line reads of this process's environment, and nothing
else: where it finds Lima, its instances, and its cache."""

PROVISION = """#!/bin/sh
set -eu
if ! command -v docker >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  apt-get install -y -q docker.io docker-buildx
fi
override=/etc/systemd/system/docker.socket.d/user.conf
want=$(printf '[Socket]\\nSocketUser=%s' "{{.User}}")
if [ "$(cat "$override" 2>/dev/null)" != "$want" ]; then
  mkdir -p "$(dirname "$override")"
  printf '%s\\n' "$want" > "$override"
  systemctl daemon-reload
  systemctl restart docker.socket docker.service
fi
systemctl enable --now docker.socket docker.service
"""
"""Docker inside the guest, from the distribution's packages, its socket
the guest user's: run at each boot, and a no-op once it holds."""

READY = """#!/bin/sh
timeout 600 sh -c 'until docker info >/dev/null 2>&1; do sleep 2; done'
"""
"""What a start waits for: Docker answering the guest user."""

IMAGE = re.compile(r"^(template:[A-Za-z0-9._/-]+|https://[^\s]+|/[^\s]+)$")
"""An image Lima builds a machine on: one of its templates, or a template's
address or path, never anything a template file would read as more."""

DISK = "disk"
"""The file of an instance's directory that holds its disk."""

CHUNK = 8 << 20
"""How much of a disk is read at once."""

SOCKET = "ssh.sock.1234567890123456"
"""The longest socket Lima keeps in an instance's folder, as Lima measures
it: an instance whose path to it reaches the host's bound on a socket's
path is refused, a clone included."""


def lima_environment() -> dict[str, str]:
    """The command line's environment: where it finds Lima, and nothing of
    the engine's credentials, so nothing of them reaches a guest."""
    return {name: os.environ[name] for name in LIMA_VARIABLES if name in os.environ}


def hypervisor() -> str | None:
    """Why this host offers no hypervisor; None when it offers one: Apple's
    virtualization on macOS, a KVM device this process may open on Linux."""
    if sys.platform == "darwin":
        try:
            answer = subprocess.run(
                ["/usr/sbin/sysctl", "-n", "kern.hv_support"],
                capture_output=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return f"sysctl cannot be read: {error}"
        if answer.stdout.strip() == b"1":
            return None
        return "this Mac offers no hypervisor (kern.hv_support)"
    if sys.platform.startswith("linux"):
        if os.access("/dev/kvm", os.R_OK | os.W_OK):
            return None
        return "this host has no /dev/kvm this process may open"
    return f"Lima runs no machine on {sys.platform}"


def lima_home() -> Path:
    """Where Lima keeps its instances, as Lima finds it: `LIMA_HOME`, else
    `.lima` in the home, its links resolved once it stands."""
    home = os.environ.get("LIMA_HOME") or os.path.join(os.path.expanduser("~"), ".lima")
    return Path(os.path.realpath(home) if os.path.exists(home) else home)


def socket_bound() -> int:
    """The bound on a socket's path on this host: 104 on macOS, 108 on
    Linux."""
    return 104 if sys.platform == "darwin" else 108


def name_refused(longest: str, home: Path, bound: int) -> str | None:
    """Why Lima, keeping its instances in `home`, refuses an instance named
    `longest`: the path to its socket reaches `bound`. None when it fits."""
    length = len(str(home / longest / SOCKET))
    if length < bound:
        return None
    return (
        f"Lima keeps its machines in {home}, and a machine named as long as "
        f"{longest!r} would put its socket {length} characters deep, where this "
        f"host allows fewer than {bound}: set LIMA_HOME to a shorter folder, or "
        "the machine prefix shorter"
    )


def template(spec: MachineSpec) -> dict[str, object]:
    """The template a machine is made from: its image, Docker provisioned
    inside, and nothing of this host in it. It mounts no directory of the
    host, forwards no port to it, carries none of its proxy variables,
    loads none of its keys, and forwards no agent."""
    if not IMAGE.match(spec.image):
        raise InfraValidationFailed(f"{spec.image!r} names no Lima template")
    made: dict[str, object] = {
        "minimumLimaVersion": "2.0.0",
        "base": [spec.image],
        "plain": True,
        "mounts": [],
        "portForwards": [],
        "propagateProxyEnv": False,
        "ssh": {"loadDotSSHPubKeys": False, "forwardAgent": False, "forwardX11": False},
        "provision": [{"mode": "system", "script": PROVISION}],
        "probes": [{"script": READY, "hint": "Docker did not answer in the guest"}],
    }
    if spec.cpus is not None:
        made["cpus"] = spec.cpus
    if spec.memory_mb is not None:
        made["memory"] = f"{spec.memory_mb}MiB"
    return made


class MachinesLimaImpl(MachinesInterface):
    """Machines through `limactl`, each command a process bounded by a
    timeout, a start by a longer one, since the first may download an image
    and install Docker. A machine's egress cannot be closed from outside its
    guest, so a machine that must reach nothing is refused; its cpus and its
    memory are held by the hypervisor. A snapshot is a clone of a stopped
    instance, held to a digest of its disk: the bytes of each run of data
    the disk holds, by offset, so the holes of a sparse disk are never
    read. `encrypted` declares the disk that holds Lima's instances
    encrypted at rest; nothing here can see it. Lima keeps a socket in
    each instance's folder, so its probe refuses a name whose socket path
    this host cannot hold, before any machine starts."""

    def __init__(
        self,
        timeout: timedelta,
        boot: timedelta,
        *,
        encrypted: bool,
        probe_hypervisor: Callable[[], str | None] = hypervisor,
    ) -> None:
        self._timeout = timeout
        self._boot = boot
        self._encrypted = encrypted
        self._hypervisor = probe_hypervisor

    async def probe(self, longest: str) -> str | None:
        refused = name_refused(longest, lima_home(), socket_bound())
        if refused is not None:
            return refused
        version = await self._limactl("--version", bound=self._timeout)
        if not version.ok:
            return f"Lima cannot be run: {version.reason()}"
        return await asyncio.to_thread(self._hypervisor)

    def closes_egress(self) -> bool:
        return False

    def limits(self) -> frozenset[str]:
        return frozenset({"cpus", "memory_mb"})

    def encrypted_at_rest(self) -> bool:
        return self._encrypted

    async def state(self, name: str) -> MachineState:
        instance = (await self._instances()).get(name)
        if instance is None:
            return MachineState.ABSENT
        return MachineState.RUNNING if instance["status"] == "Running" else MachineState.STOPPED

    async def launch(self, name: str, spec: MachineSpec, snapshot: str | None = None) -> None:
        if spec.egress_open is False:
            raise InfraValidationFailed("a Lima machine cannot be closed to egress")
        instance = (await self._instances()).get(name)
        if instance is None:
            await self._make(name, spec, snapshot)
            return
        running = instance["status"] == "Running"
        if running and _holds(instance, spec):
            return
        if running:
            await self._checked("stop", name)
        if not _holds(instance, spec):
            await self._checked("edit", *_sized(spec), name)
        await self._checked("start", name)

    async def _make(self, name: str, spec: MachineSpec, snapshot: str | None) -> None:
        """A machine made under `name`, from a clone of `snapshot` or from
        the template, and started. One that fails part way is deleted."""
        try:
            if snapshot is not None:
                if await self.state(snapshot) is not MachineState.STOPPED:
                    raise SnapshotNotFound(f"no snapshot stands under {snapshot}")
                await self._checked("clone", *_sized(spec), snapshot, name)
            else:
                with tempfile.TemporaryDirectory() as folder:
                    path = Path(folder) / f"{name}.yaml"
                    # JSON is YAML, and no value of it can be read as more.
                    path.write_text(json.dumps(template(spec)))
                    await self._checked("create", "--name", name, str(path))
            await self._checked("start", name)
        except BaseException:
            await self.destroy(name)
            raise

    async def run(
        self,
        name: str,
        argv: Sequence[str],
        *,
        bound: timedelta,
        stdin: bytes | None = None,
    ) -> MachineReply:
        return await self._limactl(*self._shell(name), *argv, bound=bound, stdin=stdin)

    def channel(self, name: str) -> Channel:
        return Channel(argv=("limactl", *self._shell(name)), env=lima_environment())

    def _shell(self, name: str) -> tuple[str, ...]:
        return ("--tty=false", "shell", "--workdir", "/", name, "--")

    async def stop(self, name: str) -> None:
        if await self.state(name) is MachineState.RUNNING:
            await self._checked("stop", name)

    async def snapshot(self, name: str, to: str) -> str:
        instances = await self._instances()
        if to not in instances:
            source = instances.get(name)
            if source is None or source["status"] != "Stopped":
                raise SnapshotNotFound(f"nothing stopped stands under {name}")
            try:
                await self._checked("clone", name, to)
            except BaseException:
                await self.destroy(to)
                raise
        digest = await self.digest(to)
        if digest is None:
            raise SnapshotNotFound(f"no snapshot stands under {to}")
        return digest

    async def digest(self, snapshot: str) -> str | None:
        instance = (await self._instances()).get(snapshot)
        if instance is None:
            return None
        return await asyncio.to_thread(_digest, Path(str(instance["dir"])) / DISK)

    async def read(self, snapshot: str) -> AsyncIterator[bytes]:
        instance = (await self._instances()).get(snapshot)
        if instance is None or instance["status"] != "Stopped":
            raise SnapshotNotFound(f"no snapshot stands under {snapshot}")
        handle = await asyncio.to_thread(os.open, Path(str(instance["dir"])) / DISK, os.O_RDONLY)
        try:
            for start, end in await asyncio.to_thread(_extents, handle):
                at = start
                while at < end:
                    part = await asyncio.to_thread(os.pread, handle, min(CHUNK, end - at), at)
                    if not part:
                        break
                    at += len(part)
                    yield part
        finally:
            os.close(handle)

    async def names(self, prefix: str) -> list[str]:
        return sorted(name for name in await self._instances() if name.startswith(prefix))

    async def destroy(self, name: str) -> None:
        # Deleting one that does not stand warns, and succeeds.
        await self._checked("delete", "--force", name)

    def describe(self) -> str:
        return "machines=lima"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def _instances(self) -> dict[str, dict[str, object]]:
        """Every instance Lima holds, by name."""
        listed = await self._limactl("--tty=false", "list", "--json", bound=self._timeout)
        if not listed.ok:
            raise BackendFailed("limactl", "list", listed.reason())
        instances: dict[str, dict[str, object]] = {}
        for line in listed.stdout.decode().splitlines():
            if line.strip():
                instance = json.loads(line)
                instances[str(instance["name"])] = instance
        return instances

    async def _checked(self, command: str, *args: str) -> None:
        bound = self._timeout if command in ("delete", "edit") else self._boot
        reply = await self._limactl("--tty=false", command, *args, bound=bound)
        if not reply.ok:
            raise BackendFailed("limactl", command, reply.reason())

    async def _limactl(
        self, *args: str, bound: timedelta, stdin: bytes | None = None
    ) -> MachineReply:
        """Runs `limactl <args>` and answers its exit and output; one that has
        not ended within `bound` is killed and answers no code. A command line
        that is not installed answers 127, as a shell would."""
        try:
            process = await asyncio.create_subprocess_exec(
                "limactl",
                *args,
                stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=lima_environment(),
                cwd="/",
            )
        except FileNotFoundError:
            return MachineReply(127, b"", b"limactl is not installed")
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(stdin), bound.total_seconds()
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            return MachineReply(None, b"", b"")
        return MachineReply(process.returncode, stdout, stderr)


def _holds(instance: dict[str, object], spec: MachineSpec) -> bool:
    """Whether an instance holds the cpus and the memory `spec` asks for."""
    if spec.cpus is not None and instance.get("cpus") != spec.cpus:
        return False
    return spec.memory_mb is None or instance.get("memory") == spec.memory_mb << 20


def _sized(spec: MachineSpec) -> tuple[str, ...]:
    """The flags that set a machine's cpus and memory to `spec`'s."""
    flags: list[str] = []
    if spec.cpus is not None:
        flags += ["--cpus", str(spec.cpus)]
    if spec.memory_mb is not None:
        flags += ["--memory", f"{spec.memory_mb / 1024:g}"]
    return tuple(flags)


def _extents(handle: int) -> list[tuple[int, int]]:
    """Where a file holds data, as runs from an offset to the next hole: a
    sparse disk's holes read as zeros, and hold nothing a command wrote."""
    size = os.fstat(handle).st_size
    runs: list[tuple[int, int]] = []
    at = 0
    while at < size:
        try:
            start = os.lseek(handle, at, os.SEEK_DATA)
        except OSError:
            break  # no data past `at`
        end = os.lseek(handle, start, os.SEEK_HOLE)
        runs.append((start, end))
        at = end
    return runs


def _digest(disk: Path) -> str:
    """A disk's digest: each run of data, by its offset and length, then its
    bytes, and the disk's size."""
    digest = hashlib.sha256()
    handle = os.open(disk, os.O_RDONLY)
    try:
        for start, end in _extents(handle):
            digest.update(f"{start}+{end - start};".encode())
            at = start
            while at < end:
                part = os.pread(handle, min(CHUNK, end - at), at)
                if not part:
                    break
                digest.update(part)
                at += len(part)
        digest.update(f"size={os.fstat(handle).st_size}".encode())
    finally:
        os.close(handle)
    return "sha256:" + digest.hexdigest()
