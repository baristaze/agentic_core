"""A process a transport starts on this host, driven to its end: its output
pumped through the command's redaction as it arrives, kept within a bound,
and its whole tree ended at the deadline.

A tree is the process, every process descended from it, and its process
group. Ending it freezes what is below the process first, walking the tree
again after each freeze, so a process that forks while it is being ended is
caught, then kills every process found and the group. A process that both leaves the
group and is orphaned before the walk escapes both; the workspace's release
is what ends it."""

import asyncio
import codecs
import os
import signal
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from acme.infra.base import utcnow
from acme.infra.transports import OutputSink
from acme.infra.transports.redaction import Redactor

GRACE_SECONDS = 2.0
"""How long the output of an ended tree is read before it is cut off."""

FREEZES = 3
"""Walks of the tree, each followed by a freeze, before the kill."""

CHUNK = 65536


async def spawn(
    argv: Sequence[str], cwd: Path, env: Mapping[str, str]
) -> asyncio.subprocess.Process:
    """Starts `argv` as the leader of a session of its own, so its group is
    its tree, with nothing on its standard input and with only `env` for an
    environment."""
    return await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=dict(env),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )


async def tree(pid: int) -> list[int]:
    """`pid` and every process descended from it, from one listing of the
    host's processes."""
    listing = await asyncio.create_subprocess_exec(
        "ps",
        "-A",
        "-o",
        "pid=",
        "-o",
        "ppid=",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await listing.communicate()
    children: dict[int, list[int]] = {}
    for line in out.decode().splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0].isdigit() and fields[1].isdigit():
            children.setdefault(int(fields[1]), []).append(int(fields[0]))
    found, frontier = [pid], [pid]
    while frontier:
        frontier = [child for parent in frontier for child in children.get(parent, [])]
        found += frontier
    return found


def _signal(pids: Sequence[int], number: signal.Signals) -> None:
    for pid in pids:
        try:
            os.kill(pid, number)
        except ProcessLookupError, PermissionError:
            pass


async def end_tree(pid: int) -> None:
    """Freezes the tree below `pid`, then kills it, `pid`, and its group.
    `pid` itself is never frozen: it is this process's child, and where the
    event loop learns of a child's end by `waitid`, a frozen child is
    reported as one that ended, and reaping it would block the loop."""
    for _ in range(FREEZES):
        _signal([found for found in await tree(pid) if found != pid], signal.SIGSTOP)
    _signal(await tree(pid), signal.SIGKILL)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError, PermissionError:
        pass


@dataclass(frozen=True)
class Driven:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    truncated: bool


class _Pump:
    """One stream: decoded, redacted with a holdback, kept to `max_output`
    characters, and passed on as it arrives."""

    def __init__(
        self,
        name: str,
        reader: asyncio.StreamReader,
        redactor: Redactor,
        max_output: int,
        on_output: OutputSink | None,
    ) -> None:
        self._name = name
        self._reader = reader
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._redaction = redactor.stream()
        self._max = max_output
        self._on_output = on_output
        self._kept: list[str] = []
        self._size = 0
        self.truncated = False

    async def run(self) -> None:
        while chunk := await self._reader.read(CHUNK):
            await self._emit(self._redaction.feed(self._decoder.decode(chunk)))
        await self._emit(self._redaction.feed(self._decoder.decode(b"", final=True)))
        await self._emit(self._redaction.flush())

    async def _emit(self, text: str) -> None:
        room = self._max - self._size
        if len(text) > room:
            self.truncated = True
            text = text[: max(room, 0)]
        if not text:
            return
        self._kept.append(text)
        self._size += len(text)
        if self._on_output is not None:
            await self._on_output(self._name, text)

    def text(self) -> str:
        """What was kept, and what the holdback still held, redacted, when
        the stream was cut off before its end."""
        rest = self._redaction.flush()
        room = self._max - self._size
        if len(rest) > room:
            self.truncated = True
        return "".join(self._kept) + rest[: max(room, 0)]


async def drive(
    process: asyncio.subprocess.Process,
    redactor: Redactor,
    *,
    max_output: int,
    deadline: datetime,
    on_output: OutputSink | None,
    end: Callable[[], Awaitable[None]],
) -> Driven:
    """Reads the process's output until it ends, ending its tree with `end`
    when the deadline comes first, and when the run is cancelled."""
    assert process.stdout is not None and process.stderr is not None
    pumps = [
        _Pump("stdout", process.stdout, redactor, max_output, on_output),
        _Pump("stderr", process.stderr, redactor, max_output, on_output),
    ]
    reading = [asyncio.create_task(pump.run()) for pump in pumps]
    waiting = asyncio.create_task(process.wait())
    timed_out = False
    try:
        done, _ = await asyncio.wait({waiting}, timeout=_left(deadline))
        if waiting not in done:
            timed_out = True
            await end()
        # The process is over; its output ends once every process that holds
        # its pipes is, and past the deadline the rest of the tree goes too.
        _, open_ = await asyncio.wait(
            reading, timeout=GRACE_SECONDS if timed_out else _left(deadline)
        )
        if open_ and not timed_out:
            await end()
            _, open_ = await asyncio.wait(open_, timeout=GRACE_SECONDS)
        for task in open_:
            task.cancel()
        await asyncio.wait({waiting}, timeout=GRACE_SECONDS)
    except BaseException:
        for task in (*reading, waiting):
            task.cancel()
        await end()
        raise
    stdout, stderr = (pump.text() for pump in pumps)
    waiting.cancel()
    return Driven(
        exit_code=None if timed_out else process.returncode,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        truncated=any(pump.truncated for pump in pumps),
    )


def _left(deadline: datetime) -> float:
    return max((deadline - utcnow()).total_seconds(), 0.0)
