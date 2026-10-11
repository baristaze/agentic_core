"""A directory on this host whose commands run as an account of their own
(ADR 1021).

A directory confines where files go and nothing else. An account confines
what a command reaches of the host: each command runs as a named account,
with its group, no supplementary group, every capability dropped from
every set, and no way to gain one back (`setpriv`), under the limits the
mode enforces (`prlimit`), in a session of its own. It reaches what the
account may, and nothing of this process's: not its files, its credential,
or its environment. No program that runs with this process's privileges
sees the command's environment: it reaches the command after the switch.

What a command left holding its output once its own process has exited
is ended as the account, which reaches the account's processes alone
(ADR 1023). The account serves one workspace at a time, so every process
of the account is that workspace's. A release ends each of them, as the
account, whatever `/proc` hides from this process, then each one in its
directory, and closes the workspace to the account, so the next
workspace's commands never reach its files. A purge clears what the account wrote, as the
account, then removes the directory without following a link out of it.

It runs under a hardened unit, which hides `/proc/sys` (`ProcSubset=pid`)
and refuses a setuid or setgid bit (`RestrictSUIDSGID=yes`): the host's
setting it needs is declared where it cannot be read, and a workspace is
shared with the account's group by a default ACL, never by a setgid bit.

What stays a product's: which account its host gives its agents, the
grants it adds to that account, and how its host image makes it."""

import asyncio
import errno
import fcntl
import os
import pwd
import re
import shutil
import struct
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from acme.infra.exceptions import InfraException
from acme.infra.workspaces import (
    EgressMode,
    IsolationMode,
    IsolationRefused,
    IsolationSpec,
    ResourceLimits,
    SnapshotRefused,
    Workspace,
    WorkspaceProviderInterface,
    refusal,
)
from acme.infra.workspaces.host import UNKEPT, remove_directory
from acme.infra.workspaces.stragglers import PROC, end_stragglers

LIMITS = frozenset({"processes"})
"""What the mode enforces of a spec's limits: the account's processes, which
are the workspace's alone (`RLIMIT_NPROC`). A share of the cpus and a bound
on memory are the whole workspace's, which no limit of one process holds,
so a spec that asks for either is refused."""

CAPABILITIES = {5: "CAP_KILL", 6: "CAP_SETGID", 7: "CAP_SETUID", 8: "CAP_SETPCAP"}
"""What this process holds to run a command as the account: the switch of
uid and gid, the drop of the bounding set, and the end of a command's tree
at its deadline."""

HARDLINKS = PROC / "sys" / "fs" / "protected_hardlinks"
"""Where the host says whether an account may link a file it does not own,
which would reach this process's files from the workspace: 1 when it may
not. A unit with `ProcSubset=pid` hides it."""

ACCOUNT_NAME = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")

SERVED = re.compile(r"[0-9a-f]{32}/[0-9a-f]{32}")
"""A workspace's directory below the root, as the account's lock names it."""

HOME, TMP = "home", "tmp"
"""Below a workspace's directory: its files, the command's home, and the
command's temporary directory."""

PASSAGE_MODE = 0o711
"""The root and a tenant's directory: passed through, never listed."""

OPEN_MODE = 0o750
"""A workspace's directory while the account serves it: the account enters
it and changes nothing in it, so what is below it stays what was made."""

CLOSED_MODE = 0o700
"""A released workspace's directory: the account reaches nothing in it."""

SHARED_MODE = 0o770
"""Its home, its temporary directory, and each directory made below them:
the account's group writes there. No setgid bit, which a hardened unit
refuses: what is made below is shared by `SHARED_BELOW`."""

SHARED_BELOW = "system.posix_acl_default"
"""The default ACL of the home and the temporary directory, which every entry
made below them takes, whoever makes it: its owner as the entry's mode
says, the account's group the same, and no one else anything."""

UMASK = 0o007
"""What a command writes stays its group's, this process's included, and no
one else's."""

CLEARING_SECONDS = 300.0
"""The longest the account's removal of what it wrote may take."""

CLEAR = "chmod -R u+rwx -- home tmp; rm -rf -- home tmp"
"""What removes, as the account, what was written in a workspace: its own
directories first opened to it, those it closed even to itself included.
Neither command follows a link it meets."""

ENDING_SECONDS = 10.0
"""The longest the end of an account's processes keeps ending them."""

END = """kill -KILL -1 2>/dev/null
left=
for status in /proc/[0-9]*/status; do
  pid=${status#/proc/}; pid=${pid%/status}
  [ "$pid" = "$$" ] && continue
  real= effective= saved= state=
  { while read -r key first second third _; do
      case $key in Uid:) real=$first effective=$second saved=$third ;; State:) state=$first ;; esac
    done < "$status"; } 2>/dev/null
  case $state in Z|X) continue ;; esac
  case " $real $effective $saved " in *" $0 "*) echo "$pid"; left=1 ;; esac
done
[ -z "$left" ]"""
"""What ends every process of the account, as the account, whose uid is
`$0`: `kill -KILL -1` signals each one the account may, whatever this
process's view of `/proc` hides, and no fork escapes it. Then the account's
own view of `/proc` is read for one still alive, other than this shell; a
zombie is dead, whoever reaps it. It answers 0 when none is, and prints
each one alive otherwise. It runs builtins alone, so it starts no process
of the account."""


END_GROUP = """group=$0 found=' ' pass=0
kill -STOP "-$group" 2>/dev/null
while [ "$pass" -lt 3 ]; do
  for stat in /proc/[0-9]*/stat; do
    pid=${stat#/proc/}; pid=${pid%/stat}
    [ "$pid" = "$$" ] && continue
    case $found in *" $pid "*) continue ;; esac
    line=
    { read -r line < "$stat"; } 2>/dev/null
    set -f; set -- ${line##*) }; set +f
    if [ "$3" != "$group" ]; then
      case $found in *" $2 "*) ;; *) continue ;; esac
    fi
    kill -STOP "$pid" 2>/dev/null
    found="$found$pid "
  done
  pass=$((pass + 1))
done
kill -KILL "-$group" $found 2>/dev/null
exit 0"""
"""What ends, as the account, what a command whose group is `$0` left once
its own process is over: each process of the group, and each one descended
from one, frozen as the account's own view of `/proc` shows it, walked again
after each freeze, then killed with the group. A process's name may hold
spaces and parentheses, so its parent and its group are read after the last
`) `. As the account, it signals the account's processes alone, whichever
process a pid names by then. It runs builtins alone, so it starts no
process of the account."""


def temporary(home: Path) -> Path:
    """The temporary directory of the workspace whose home is `home`."""
    return home.parent / TMP


@dataclass(frozen=True)
class Switch:
    """How this process runs a command as `account`."""

    account: str
    uid: int
    gid: int
    setpriv: str
    prlimit: str

    def argv(
        self, script: str, args: Sequence[str] = (), limits: ResourceLimits | None = None
    ) -> list[str]:
        """`sh -c script args` as the account: its limits lowered first, hard
        and soft, while this process may, so it cannot raise them again; then
        its uid and gid, no supplementary group, every capability dropped
        from every set, and `no_new_privs`."""
        processes = limits.processes if limits is not None else None
        lowered = [self.prlimit, f"--nproc={processes}", "--"] if processes else []
        return [
            *lowered,
            self.setpriv,
            f"--reuid={self.uid}",
            f"--regid={self.gid}",
            "--clear-groups",
            "--inh-caps=-all",
            "--ambient-caps=-all",
            "--bounding-set=-all",
            "--no-new-privs",
            "--",
            "/bin/sh",
            "-c",
            script,
            *args,
        ]


class ProcessesOutlived(InfraException):
    """Processes of an account still alive after their end: what is left of
    one workspace could act in the next."""

    code = "processes_outlived"


def effective_capabilities() -> set[int]:
    """The capabilities in this process's effective set; none where `/proc`
    does not show them."""
    try:
        status = (PROC / "self" / "status").read_text()
    except OSError:
        return set()
    found = re.search(r"^CapEff:\s*([0-9a-fA-F]+)$", status, re.MULTILINE)
    mask = int(found.group(1), 16) if found else 0
    return {bit for bit in range(64) if mask >> bit & 1}


def linking_refusal(declared: bool) -> str | None:
    """Why this host may let an account link a file it does not own; None
    when it keeps it from doing so. Its setting decides where this process
    can read it, whatever is declared; where it cannot, as under a unit
    with `ProcSubset=pid`, the runner's declaration that it is on decides."""
    try:
        setting = HARDLINKS.read_text().strip()
    except OSError:
        if declared:
            return None
        return (
            "this process cannot read fs.protected_hardlinks, and the runner's "
            "settings do not declare it on: an account may link a file it does not own"
        )
    if setting == "1":
        return None
    return "this host lets an account link a file it does not own (fs.protected_hardlinks is not 1)"


def shared_below(gid: int) -> bytes:
    """`SHARED_BELOW` as the kernel stores it: version 2, then each entry's
    tag, permissions, and id, in the kernel's order. The owner and the group
    `gid` hold what the mask lets through, which an entry's mode sets; the
    owning group and others hold nothing."""
    unnamed = 0xFFFFFFFF
    entries = (
        (0x01, 0o7, unnamed),  # the owner
        (0x04, 0o0, unnamed),  # the owning group
        (0x08, 0o7, gid),  # the account's group
        (0x10, 0o7, unnamed),  # the mask
        (0x20, 0o0, unnamed),  # others
    )
    return struct.pack("<I", 2) + b"".join(struct.pack("<HHI", *entry) for entry in entries)


def switch_to(account: str) -> Switch:
    """How this process runs a command as `account`. `IsolationRefused`
    naming what this host lacks: Linux; the account, apart from root and
    this process's own; this process in the account's group, which its
    workspaces are shared through; `setpriv` and `prlimit`; and the
    capabilities the switch takes."""
    if sys.platform != "linux" or not PROC.is_dir():
        raise IsolationRefused("an account workspace runs on Linux alone")
    if not ACCOUNT_NAME.match(account):
        raise IsolationRefused(f"{account!r} is not the name of an account")
    try:
        entry = pwd.getpwnam(account)
    except KeyError:
        raise IsolationRefused(f"the account {account!r} is not on this host") from None
    if entry.pw_uid in {0, os.getuid(), os.geteuid()} or entry.pw_gid == 0:
        raise IsolationRefused(f"the account {account!r} is root's, or this process's own")
    if entry.pw_gid not in {os.getgid(), *os.getgroups()}:
        raise IsolationRefused(
            f"this process is not in the group of {account!r}, which its workspaces are shared through"
        )
    setpriv, prlimit = shutil.which("setpriv"), shutil.which("prlimit")
    if setpriv is None or prlimit is None:
        raise IsolationRefused("setpriv and prlimit (util-linux) are not both on PATH")
    held = effective_capabilities()
    missing = [name for bit, name in CAPABILITIES.items() if bit not in held]
    if missing:
        raise IsolationRefused(f"this process lacks {', '.join(missing)} to run as {account!r}")
    return Switch(account, entry.pw_uid, entry.pw_gid, setpriv, prlimit)


async def end_account(switch: Switch, seconds: float = ENDING_SECONDS) -> None:
    """Ends every process of the account, whatever its directory, as the
    account (`END`), again until the account's own view of `/proc` shows
    none alive. `ProcessesOutlived` when some are after `seconds`, or the
    end cannot run."""
    deadline = time.monotonic() + seconds
    while True:
        ending = await asyncio.create_subprocess_exec(
            *switch.argv(END, (str(switch.uid),)),
            cwd="/",
            env={},
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            out, _ = await asyncio.wait_for(
                ending.communicate(), max(deadline - time.monotonic(), 1.0)
            )
        except TimeoutError:
            ending.kill()
            await ending.wait()
            out = b""
        if ending.returncode == 0:
            return
        if time.monotonic() > deadline:
            alive = " ".join(out.decode(errors="replace").split()) or "unknown, the end did not run"
            raise ProcessesOutlived(
                f"processes of {switch.account!r} alive after {seconds}s: {alive}"
            )
        await asyncio.sleep(0.05)


async def end_group_as(switch: Switch, group: int, seconds: float = ENDING_SECONDS) -> None:
    """Ends, as the account, what a command whose group is `group` left
    (`END_GROUP`). An end that has not finished after `seconds` is cut off;
    what it left then ends with the workspace's release."""
    ending = await asyncio.create_subprocess_exec(
        *switch.argv(END_GROUP, (str(group),)),
        cwd="/",
        env={},
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        await asyncio.wait_for(ending.wait(), seconds)
    except TimeoutError:
        ending.kill()
        await ending.wait()


class WorkspaceAccountImpl(WorkspaceProviderInterface):
    """A directory per workspace under `root`, its commands run as `account`
    by the local transport given the same account. It meets the account
    mode with open egress and a bound on processes, and refuses any spec
    that asks for more, and any host that cannot switch to the account.

    The account serves one workspace at a time, across every process that
    shares the root: a prepare while it serves another is refused, and a
    purge then removes only what this process can. A host that lets an
    account link a file it does not own is refused too: the link would
    reach this process's files from the workspace. Where this process cannot
    read the host's setting, `protected_hardlinks` is the runner's word that
    it is on. Like any directory on a host, it never snapshots a workspace
    (`UNKEPT`)."""

    def __init__(self, root: Path, account: str, *, protected_hardlinks: bool = False) -> None:
        self._root = root
        self._account = account
        self._protected_hardlinks = protected_hardlinks
        self._turn = asyncio.Lock()
        self._held: UUID | None = None
        self._lock: int | None = None

    async def prepare(
        self,
        org_id: UUID,
        workspace_id: UUID,
        spec: IsolationSpec,
        snapshot: bytes | None = None,
    ) -> Workspace:
        why = refusal(
            spec, mode=IsolationMode.ACCOUNT, egress={EgressMode.OPEN}, limits=LIMITS, unkept=UNKEPT
        )
        if why is None and snapshot is not None:
            why = f"an account workspace cannot start from a snapshot: {UNKEPT}"
        if why is not None:
            raise IsolationRefused(why)
        switch = await asyncio.to_thread(switch_to, self._account)
        linking = await asyncio.to_thread(linking_refusal, self._protected_hardlinks)
        if linking is not None:
            raise IsolationRefused(linking)
        job = self._job(org_id, workspace_id)
        async with self._turn:
            fresh = self._held is None
            if not await self._took(workspace_id, job, switch):
                raise IsolationRefused(
                    f"the account {self._account!r} serves another workspace, and one at a time",
                    clears=True,
                )
            try:
                await asyncio.to_thread(_opened, job, switch.gid)
            except BaseException:
                if fresh:
                    self._give_back()
                raise
        return Workspace(id=workspace_id, org_id=org_id, spec=spec, location=str(job / HOME))

    async def snapshot(self, workspace: Workspace) -> bytes:
        raise SnapshotRefused(f"an account workspace cannot be snapshotted: {UNKEPT}")

    async def release(self, workspace: Workspace) -> None:
        job = self._job(workspace.org_id, workspace.id)
        switch = await asyncio.to_thread(switch_to, self._account)
        async with self._turn:
            if not await self._took(workspace.id, job, switch):
                await asyncio.to_thread(_closed, job)
                return
            try:
                await end_account(switch)
                await end_stragglers(job)
            finally:
                await asyncio.to_thread(_closed, job)
                self._give_back()

    async def purge(self, org_id: UUID, workspace_id: UUID) -> None:
        job = self._job(org_id, workspace_id)
        if not await asyncio.to_thread(job.is_dir):
            return
        switch = await asyncio.to_thread(switch_to, self._account)
        async with self._turn:
            if not await self._took(workspace_id, job, switch):
                # None of its processes is alive, and the account reaches
                # nothing in it: what only the account can remove stays, and
                # the purge fails, to be tried again.
                await asyncio.to_thread(remove_directory, job)
                return
            try:
                await end_account(switch)
                await end_stragglers(job)
                await self._cleared(job, switch)
                await asyncio.to_thread(remove_directory, job)
            finally:
                self._give_back()

    def describe(self) -> str:
        return f"workspaces=account({self._account}, {self._root})"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        self._give_back()

    def _job(self, org_id: UUID, workspace_id: UUID) -> Path:
        """Named by ids alone, so no name climbs out of the root."""
        return self._root.resolve() / org_id.hex / workspace_id.hex

    async def _took(self, workspace_id: UUID, job: Path, switch: Switch) -> bool:
        """Whether the account serves `workspace_id`, whose directory is
        `job`, now: it did already, or it served none, in this process or
        another, and now does. What a crash left of the workspace it served
        last is ended first, its processes, then its directory's opening."""
        if self._held is not None:
            return self._held == workspace_id
        lock = await asyncio.to_thread(_locked, self._root, self._account)
        if lock is None:
            return False
        try:
            await end_account(switch)
            await asyncio.to_thread(_handed_over, lock, self._root.resolve(), job)
        except BaseException:
            os.close(lock)
            raise
        self._held, self._lock = workspace_id, lock
        return True

    def _give_back(self) -> None:
        if self._lock is not None:
            os.close(self._lock)
        self._held = self._lock = None

    async def _cleared(self, job: Path, switch: Switch) -> None:
        """Removes, as the account, what was written in the workspace. No
        other process of the account is alive, so it may enter."""
        await asyncio.to_thread(os.chmod, job, OPEN_MODE)
        clearing = await asyncio.create_subprocess_exec(
            *switch.argv(CLEAR),
            cwd=job,
            env={},
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
            umask=UMASK,
        )
        try:
            await asyncio.wait_for(clearing.wait(), CLEARING_SECONDS)
        except TimeoutError:
            pass
        finally:
            await end_account(switch)
            await clearing.wait()


def _locked(root: Path, account: str) -> int | None:
    """A lock on the account under `root`, held until its descriptor closes;
    None when another holder has it. Only this process's account opens it."""
    if not root.is_dir():
        root.mkdir(parents=True, exist_ok=True)
        os.chmod(root, PASSAGE_MODE)
    lock = os.open(root / f".account-{account}.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock)
        return None
    return lock


def _handed_over(lock: int, root: Path, job: Path) -> None:
    """Closes the directory of the workspace the account served last, which
    a crash may have left open to it, and names `job` in the lock as the
    one it serves now."""
    last = os.pread(lock, 128, 0).decode(errors="replace")
    if SERVED.fullmatch(last):
        _closed(root / last)
    os.ftruncate(lock, 0)
    os.pwrite(lock, str(job.relative_to(root)).encode(), 0)


def _opened(job: Path, gid: int) -> None:
    """The workspace's directory, made where it is missing, and opened to the
    account: its home and its temporary directory shared with the account's
    group, and what is made below them too, then the directory itself, last.
    `IsolationRefused` where the filesystem keeps no ACL."""
    if sys.platform != "linux":
        raise IsolationRefused("an account workspace runs on Linux alone")
    job.parent.mkdir(exist_ok=True)
    os.chmod(job.parent, PASSAGE_MODE)
    job.mkdir(mode=CLOSED_MODE, exist_ok=True)
    for below in (job / HOME, job / TMP):
        below.mkdir(mode=CLOSED_MODE, exist_ok=True)
        os.chown(below, -1, gid)
        os.chmod(below, SHARED_MODE)
        try:
            os.setxattr(below, SHARED_BELOW, shared_below(gid), follow_symlinks=False)
        except OSError as error:
            if error.errno != errno.EOPNOTSUPP:
                raise
            raise IsolationRefused(
                f"the filesystem of {job} keeps no ACL, which a workspace is shared through"
            ) from None
    os.chown(job, -1, gid)
    os.chmod(job, OPEN_MODE)


def _closed(job: Path) -> None:
    try:
        os.chmod(job, CLOSED_MODE)
    except FileNotFoundError:
        pass
