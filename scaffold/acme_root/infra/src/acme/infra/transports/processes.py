"""A process a transport starts on this host, driven to its end: its output
pumped through the command's redaction as it arrives, kept within a bound
(past it, its head and its tail, where a command's end, its summary or the
error that stopped it, usually is), and its whole tree ended at the
deadline.

A command is over when its own process exits. Its output is read on for a
short bound (`DRAIN_SECONDS`), and what still holds it then is ended: every
process of its group, and every one descended from one. Its own pid may name
another process by then; its group cannot while a process of it lives. So a
process a command left holding its output never holds the command to its
deadline.

A tree is the process, every process descended from it, and its process
group. Ending it freezes what is below the process first, walking the tree
again after each freeze, so a process that forks while it is being ended is
caught, then kills every process found and the group. A process that both
leaves the group and is orphaned before the walk escapes both; the
workspace's release is what ends it."""

import asyncio
import codecs
import logging
import os
import signal
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from acme.infra.base import utcnow
from acme.infra.transports import OutputSink
from acme.infra.transports.redaction import Redactor

log = logging.getLogger(__name__)

GRACE_SECONDS = 2.0
"""How long the output of an ended tree is read before it is cut off."""

DRAIN_SECONDS = 2.0
"""How long a command's output is read once its own process has exited,
before what still holds it is ended."""

EXIT_POLL_SECONDS = 0.05
"""How often a running command's own process is looked at for its exit."""

FREEZES = 3
"""Walks of the tree, each followed by a freeze, before the kill."""

CHUNK = 65536

PROC = Path("/proc")


async def spawn(
    argv: Sequence[str],
    cwd: Path,
    env: Mapping[str, str],
    *,
    umask: int = -1,
    stdin: int = asyncio.subprocess.DEVNULL,
) -> asyncio.subprocess.Process:
    """Starts `argv` as the leader of a session of its own, so its group is
    its tree, with only `env` for an environment, with nothing on its
    standard input unless `stdin` names a descriptor, and with `umask` where
    one is given."""
    return await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=dict(env),
        stdin=stdin,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        umask=umask,
    )


async def tree(pid: int) -> list[int]:
    """`pid` and every process descended from it, from one listing of the
    host's processes: `ps` where the host has it, `/proc` where it has not,
    as in a slim image. With neither, `pid` alone, and its group still ends
    with it."""
    return _below(await _listing(), [pid])


async def of_group(group: int) -> list[int]:
    """Every process of the group `group`, and every process descended from
    one, from one listing of the host's processes."""
    listing = await _listing()
    return _below(listing, [pid for pid, _, found in listing if found == group])


def _below(listing: Sequence[tuple[int, int, int]], roots: Sequence[int]) -> list[int]:
    """`roots` and every process descended from one, each once."""
    children: dict[int, list[int]] = {}
    for pid, parent, _ in listing:
        children.setdefault(parent, []).append(pid)
    found = list(dict.fromkeys(roots))
    seen, frontier = set(found), found
    while frontier:
        frontier = [
            child for parent in frontier for child in children.get(parent, []) if child not in seen
        ]
        seen.update(frontier)
        found += frontier
    return found


async def _listing() -> list[tuple[int, int, int]]:
    """Each process, as its pid, its parent's, and its group's."""
    try:
        listing = await asyncio.create_subprocess_exec(
            "ps",
            "-A",
            "-o",
            "pid=",
            "-o",
            "ppid=",
            "-o",
            "pgid=",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return await asyncio.to_thread(_listing_in_proc)
    out, _ = await listing.communicate()
    if listing.returncode != 0:
        return await asyncio.to_thread(_listing_in_proc)
    found: list[tuple[int, int, int]] = []
    for line in out.decode().splitlines():
        fields = line.split()
        if len(fields) == 3 and all(field.isdigit() for field in fields):
            found.append((int(fields[0]), int(fields[1]), int(fields[2])))
    return found


def _listing_in_proc() -> list[tuple[int, int, int]]:
    """Each process, read off `/proc/<pid>/stat`; none where the host has no
    `/proc`. A process's name may hold spaces and parentheses, so its parent
    and its group are read after the last `)`."""
    found: list[tuple[int, int, int]] = []
    if not PROC.is_dir():
        return found
    for entry in PROC.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
        except OSError:
            continue
        fields = stat[stat.rfind(")") + 1 :].split()  # state, parent, group, ...
        if len(fields) > 2 and fields[1].isdigit() and fields[2].isdigit():
            found.append((int(entry.name), int(fields[1]), int(fields[2])))
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
    reported as one that ended, and reaping it would block the loop. Whatever
    the walk meets, `pid` and its group are killed."""
    try:
        for _ in range(FREEZES):
            _signal([found for found in await tree(pid) if found != pid], signal.SIGSTOP)
        _signal(await tree(pid), signal.SIGKILL)
    finally:
        _signal([pid], signal.SIGKILL)
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError, PermissionError:
            pass


async def end_group(group: int) -> None:
    """Freezes every process of the group `group` and every process below
    one, walking again after each freeze, then kills each one found and the
    group: what a command left, once its own process is over."""
    try:
        for _ in range(FREEZES):
            _signal(await of_group(group), signal.SIGSTOP)
        _signal(await of_group(group), signal.SIGKILL)
    finally:
        try:
            os.killpg(group, signal.SIGKILL)
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
    characters (past them, its head and its tail, half each), and passed on
    as it arrives while it is within them."""

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
        self._head: list[str] = []
        self._head_size = 0
        self._tail: deque[str] = deque()
        self._tail_size = 0
        self._tail_room = max_output // 2
        self._head_room = max_output - self._tail_room
        self._seen = 0
        self._streamed = 0
        self._viewer_failed = False
        self.truncated = False

    async def run(self) -> None:
        while chunk := await self._reader.read(CHUNK):
            await self._emit(self._redaction.feed(self._decoder.decode(chunk)))
        await self._emit(self._redaction.feed(self._decoder.decode(b"", final=True)))
        await self._emit(self._redaction.flush())

    async def _emit(self, text: str) -> None:
        """Keeps the text and passes it on while the stream is within its
        bound. A viewer that fails costs the view, never the command: its
        output is still read and kept."""
        if not text:
            return
        self._keep(text)
        room = self._max - self._streamed
        if self._on_output is None or room <= 0:
            return
        shown = text[:room]
        self._streamed += len(shown)
        try:
            await self._on_output(self._name, shown)
        except Exception:
            if not self._viewer_failed:
                self._viewer_failed = True
                log.warning("a viewer of %s failed; the command goes on", self._name, exc_info=True)

    def _keep(self, text: str) -> None:
        """The head fills first; past it, the tail keeps the latest text,
        dropping its oldest pieces while what is left still fills it."""
        if not text:
            return
        self._seen += len(text)
        room = self._head_room - self._head_size
        if room > 0:
            self._head.append(text[:room])
            self._head_size += min(room, len(text))
            text = text[room:]
        if text:
            self._tail.append(text)
            self._tail_size += len(text)
            while self._tail and self._tail_size - len(self._tail[0]) >= self._tail_room:
                self._tail_size -= len(self._tail.popleft())

    def text(self) -> str:
        """What was kept, the holdback's text included when the stream was
        cut off before its end, redacted: all of it within the bound, else
        its head and its tail with a line between them saying what was cut."""
        self._keep(self._redaction.flush())
        head, tail = "".join(self._head), "".join(self._tail)
        if self._seen <= self._max:
            return head + tail
        self.truncated = True
        return cut_between(head, tail[len(tail) - self._tail_room :], self._seen - self._max)


def cut_between(head: str, tail: str, cut: int) -> str:
    """A stream past its bound: its head, a line naming how many characters
    were cut, and its tail."""
    return f"{head}\n[{cut} characters cut here]\n{tail}"


def bound_text(text: str, limit: int) -> tuple[str, bool]:
    """A whole stream within `limit` characters, as `_Pump` keeps one, and
    whether it was cut: as it is, or its head and its tail, half each."""
    if len(text) <= limit:
        return text, False
    tail = limit // 2
    return cut_between(text[: limit - tail], text[len(text) - tail :], len(text) - limit), True


async def drive(
    process: asyncio.subprocess.Process,
    redactor: Redactor,
    *,
    max_output: int,
    deadline: datetime,
    on_output: OutputSink | None,
    end: Callable[[], Awaitable[None]],
    end_left: Callable[[], Awaitable[None]],
) -> Driven:
    """Reads the process's output until the process is over. At the deadline,
    and when the run is cancelled, its tree ends with `end`. Once it has
    exited by itself, its output is read for `DRAIN_SECONDS`, and what still
    holds it then ends with `end_left`, as it does when the run is cancelled
    after the exit."""
    assert process.stdout is not None and process.stderr is not None
    pumps = [
        _Pump("stdout", process.stdout, redactor, max_output, on_output),
        _Pump("stderr", process.stderr, redactor, max_output, on_output),
    ]
    reading = [asyncio.create_task(pump.run()) for pump in pumps]
    waiting = asyncio.create_task(_exited(process))
    timed_out = False
    try:
        done, _ = await asyncio.wait({waiting}, timeout=_left(deadline))
        if waiting not in done:
            timed_out = True
            await end()
            _, open_ = await asyncio.wait(reading, timeout=GRACE_SECONDS)
        else:
            _, open_ = await asyncio.wait(reading, timeout=DRAIN_SECONDS)
            if open_:
                await end_left()
                _, open_ = await asyncio.wait(open_, timeout=GRACE_SECONDS)
        for task in open_:
            task.cancel()
        await asyncio.wait({waiting}, timeout=GRACE_SECONDS)
    except BaseException:
        for task in (*reading, waiting):
            task.cancel()
        await (end() if process.returncode is None else end_left())
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


async def _exited(process: asyncio.subprocess.Process) -> None:
    """Returns once the process has exited. Its return code is set
    at its exit, while `Process.wait()` returns, on CPython before 3.14.7,
    only once every pipe of it has closed: a process it left holding its
    output would hold the command to its deadline."""
    while process.returncode is None:
        await asyncio.sleep(EXIT_POLL_SECONDS)


def _left(deadline: datetime) -> float:
    return max((deadline - utcnow()).total_seconds(), 0.0)
