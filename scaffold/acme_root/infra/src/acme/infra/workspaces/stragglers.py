"""Processes a workspace's commands left running in a directory on this
host after they ended: a server put in the background with its output sent
elsewhere, a watcher, a daemon that left its group. A command's own tree
ends at its deadline (`acme.infra.transports.processes`); a command that
ends by itself first leaves these behind, and the workspace's release is
what ends them.

A process is the workspace's when its working directory is inside the
workspace's directory. Each one is ended by its own id, never by its group,
so nothing whose directory is elsewhere is touched, and never this process,
its parent, or one of its group. They are found on `/proc` where the host
has it, by `lsof` where it has not, and not at all where it has neither."""

import asyncio
import os
import shutil
import signal
from collections.abc import Iterable, Mapping
from pathlib import Path

PROC = Path("/proc")

FREEZES = 3
"""Walks of the workspace's processes, each followed by a freeze, before
the kill, so one that forks while it is being ended is caught."""

LISTING_SECONDS = 10.0
"""The longest a listing of the host's processes is waited on."""


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root + os.sep)


def _in_proc(root: str) -> dict[int, int]:
    """Each process whose working directory, as `/proc` links it, is inside
    `root`, with its parent's id. A process's name may hold spaces and
    parentheses, so its parent is read after the last `)` of its `stat`."""
    found: dict[int, int] = {}
    for entry in PROC.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            directory = os.readlink(entry / "cwd")
            stat = (entry / "stat").read_text()
        except OSError:
            continue
        fields = stat[stat.rfind(")") + 1 :].split()  # state, parent, group, ...
        if _inside(directory, root) and len(fields) > 1 and fields[1].isdigit():
            found[int(entry.name)] = int(fields[1])
    return found


async def _by_lsof(root: str) -> dict[int, int]:
    """Each process whose working directory, as `lsof` names it, is inside
    `root`, with its parent's id: one listing of every process's working
    directory, its id on a `p` line, its parent's on an `R` line, and the
    directory on an `n` line."""
    lsof = shutil.which("lsof")
    if lsof is None:
        return {}
    listing = await asyncio.create_subprocess_exec(
        lsof,
        "-n",
        "-w",
        "-d",
        "cwd",
        "-F",
        "pRn",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        out, _ = await asyncio.wait_for(listing.communicate(), LISTING_SECONDS)
    except TimeoutError:
        listing.kill()
        await listing.wait()
        return {}
    found: dict[int, int] = {}
    pid = parent = 0
    for line in out.decode(errors="replace").splitlines():
        field, value = line[:1], line[1:]
        if field == "p" and value.isdigit():
            pid, parent = int(value), 0
        elif field == "R" and value.isdigit():
            parent = int(value)
        elif field == "n" and pid and _inside(value, root):
            found[pid] = parent
    return found


def _group(pid: int) -> int | None:
    try:
        return os.getpgid(pid)
    except OSError:
        return None


async def stragglers(root: Path) -> dict[int, int]:
    """The processes whose working directory is inside `root`, each with its
    parent's id, other than this process, its parent, and those of its
    group."""
    where, proc = await asyncio.to_thread(lambda: (os.path.realpath(root), PROC.is_dir()))
    found = await asyncio.to_thread(_in_proc, where) if proc else await _by_lsof(where)
    spared, mine = {1, os.getpid(), os.getppid()}, os.getpgrp()
    return {
        pid: parent
        for pid, parent in found.items()
        if pid not in spared and _group(pid) not in (None, mine)
    }


def _signal(pids: Iterable[int], number: signal.Signals) -> None:
    for pid in pids:
        try:
            os.kill(pid, number)
        except ProcessLookupError, PermissionError:
            pass


def _freezable(found: Mapping[int, int]) -> list[int]:
    """What may be frozen: none of this process's own children. Where the
    event loop learns of a child's end by `waitid`, a frozen child is
    reported as one that ended, and reaping it would block the loop."""
    me = os.getpid()
    return [pid for pid, parent in found.items() if parent != me]


async def end_stragglers(root: Path) -> set[int]:
    """Freezes each process `stragglers` finds, walking again after each
    freeze, then kills every one found. Answers the ids it ended; none
    when `root` does not exist."""
    if not await asyncio.to_thread(root.is_dir):
        return set()
    found: dict[int, int] = {}
    for _ in range(FREEZES):
        walked = await stragglers(root)
        _signal(_freezable(walked), signal.SIGSTOP)
        if walked.keys() <= found.keys():
            break
        found |= walked
    _signal(found, signal.SIGKILL)
    return set(found)
