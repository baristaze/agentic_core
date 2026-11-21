"""A workspace's directory packed, read out, and brought back where its
instance runs, through a channel: the command line on the engine's host that
runs a command there (`acme.infra.vms.Channel`). The VM provider keeps a
VM's directory this way, and a provider of a layer above that reaches its
instances by a channel of its own keeps theirs the same way (ADR 1045).

A pack is one tar archive of the directory, written in the instance's
temporary space at a path named before tar starts, so it is taken at one
moment and measured before any byte of it leaves. The directory is measured
first, by the bytes its files hold: one past the bound is refused before it
is packed, with its size and the bound. A pack is bounded by a time of its
own, and one that fails, outlasts it, or is cancelled is removed, so no pack
is left behind. It is read out as a stream, in parts, as often as its caller
asks, then dropped. A restore empties the directory and unpacks a stream
into it. Each runs as the instance's root where its user may become root
without a password, as a VM's may, so a file a command wrote as root is kept
and brought back as it was; elsewhere, as its user.

A stream is bounded by how long it may stall, never by how long it runs: a
part that does not move within `stall` ends it, and a large directory that
keeps moving is read whole."""

import asyncio
import contextlib
import secrets
from collections.abc import AsyncGenerator, AsyncIterable, Sequence
from dataclasses import dataclass
from datetime import timedelta

from acme.infra.agent_workspaces import SnapshotRefused
from acme.infra.exceptions import BackendFailed
from acme.infra.vms import Channel

__all__ = ["drop", "pack", "read", "unpack"]

AS_ROOT = 'as=; if [ "$(id -u)" != 0 ] && sudo -n true 2>/dev/null; then as="sudo -n"; fi'
"""Root where the instance's user may become it without a password."""

TEMPORARY = "/var/tmp"
"""Where a pack is written in the instance."""

PAST = 3
"""The pack script's exit when the directory holds more than the bound."""

PACK = f"""set -u
{AS_ROOT}
held=$($as find "$1" -type f -exec wc -c {{}} + \\
  | awk '!(NF == 2 && $2 == "total") {{ s += $1 }} END {{ printf "%.0f\\n", s }}')
if [ "$held" -gt "$3" ]; then printf '%s\\n' "$held"; exit {PAST}; fi
set -C
: > "$2" || exit 70
set +C
$as tar --numeric-owner -C "$1" -cf - . > "$2"
code=$?
if [ "$code" -gt 1 ]; then exit "$code"; fi
size=$(wc -c < "$2") || exit 70
printf '%s\\n' $size
"""
"""The directory `$1` measured by the bytes its files hold, and refused past
the bound `$3`; else packed at `$2`, a path no file stands at, and the
pack's size. Tar's exit 1 says a file changed while it was read: the pack
holds it as read."""

UNPACK = f"""set -u
{AS_ROOT}
$as find "$1" -mindepth 1 -delete || exit $?
exec $as tar --numeric-owner -C "$1" -xf -
"""
"""The directory `$1` emptied, then filled from the archive on the standard
input."""


@dataclass(frozen=True)
class _Ended:
    code: int | None  # None when it stalled
    stdout: bytes
    stderr: bytes

    def reason(self) -> str:
        if self.code is None:
            return "the instance did not answer in time"
        lines = self.stderr.decode(errors="replace").strip().splitlines()
        return lines[-1] if lines else f"the command exited {self.code}"


async def pack(
    channel: Channel, directory: str, *, bound: int, within: timedelta, stall: timedelta
) -> tuple[str, int]:
    """`directory`, from the instance's root, packed beside it: the pack's
    path there and its size in bytes. One whose files hold more than `bound`
    bytes is refused (`SnapshotRefused`) before it is packed. A pack that
    fails, takes longer than `within`, or is cancelled leaves no pack: its
    path is named before tar starts, and removed however it ends."""
    path = f"{TEMPORARY}/acme-pack.{secrets.token_hex(8)}"
    argv = ("sh", "-c", PACK, "sh", directory, path, str(bound))
    try:
        ended = await _run(channel, argv, stall=within)
        if ended.code == PAST:
            held = ended.stdout.decode(errors="replace").strip()
            raise SnapshotRefused(
                f"the workspace's directory holds {held} bytes, "
                f"past the bound of {bound}, so no snapshot is kept"
            )
        if ended.code is None:
            raise BackendFailed(
                "directory", "pack", f"the pack did not end within {_seconds(within):g} seconds"
            )
        if ended.code != 0:
            raise BackendFailed("directory", "pack", ended.reason())
        try:
            return path, int(ended.stdout)
        except ValueError as error:
            raise BackendFailed(
                "directory", "pack", f"the pack is not measured: {error}"
            ) from error
    except BaseException:
        # A tar that outlived its command line writes on to a file no path
        # names, and its space comes back once it ends.
        with contextlib.suppress(Exception):
            await drop(channel, path, stall=stall)
        raise


async def read(
    channel: Channel, path: str, part_bytes: int, *, stall: timedelta
) -> AsyncGenerator[bytes]:
    """The pack at `path`, in parts of `part_bytes`, the last one shorter."""
    if part_bytes <= 0:
        raise ValueError("a part holds at least one byte")
    process = await _spawn(channel, ("cat", "--", path), stdin=False)
    assert process.stdout is not None and process.stderr is not None
    errors = asyncio.ensure_future(process.stderr.read())
    try:
        while True:
            part = await asyncio.wait_for(_fill(process.stdout, part_bytes), _seconds(stall))
            if part:
                yield part
            if len(part) < part_bytes:
                break
        code = await asyncio.wait_for(process.wait(), _seconds(stall))
        stderr = await asyncio.wait_for(errors, _seconds(stall))
    except TimeoutError as error:
        raise BackendFailed("directory", "read", "the instance did not answer in time") from error
    finally:
        await _end(process, errors)
    if code != 0:
        raise BackendFailed("directory", "read", _Ended(code, b"", stderr).reason())


async def drop(channel: Channel, path: str, *, stall: timedelta) -> None:
    """The pack at `path` removed; one already gone is no error."""
    ended = await _run(channel, ("rm", "-f", "--", path), stall=stall)
    if ended.code != 0:
        raise BackendFailed("directory", "drop", ended.reason())


async def unpack(
    channel: Channel, directory: str, parts: AsyncIterable[bytes], *, stall: timedelta
) -> None:
    """`directory`, from the instance's root, emptied and filled from the
    archive whose bytes `parts` hold in order. An error `parts` raises ends
    the unpack and is raised as it is."""
    process = await _spawn(channel, ("sh", "-c", UNPACK, "sh", directory), stdin=True)
    assert process.stdin is not None and process.stderr is not None
    errors = asyncio.ensure_future(process.stderr.read())
    try:
        try:
            async for part in parts:
                process.stdin.write(part)
                await asyncio.wait_for(process.stdin.drain(), _seconds(stall))
            process.stdin.close()
        except BrokenPipeError, ConnectionResetError:
            # The archive ended its reader early: its exit says why.
            pass
        code = await asyncio.wait_for(process.wait(), _seconds(stall))
        stderr = await asyncio.wait_for(errors, _seconds(stall))
    except TimeoutError as error:
        raise BackendFailed("directory", "unpack", "the instance did not answer in time") from error
    finally:
        await _end(process, errors)
    if code != 0:
        raise BackendFailed("directory", "unpack", _Ended(code, b"", stderr).reason())


async def _fill(stream: asyncio.StreamReader, size: int) -> bytes:
    """`size` bytes of `stream`, or what is left of it, shorter, at its end."""
    try:
        return await stream.readexactly(size)
    except asyncio.IncompleteReadError as short:
        return short.partial


async def _spawn(
    channel: Channel, argv: Sequence[str], *, stdin: bool
) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        *channel.argv,
        *argv,
        env=dict(channel.env),
        stdin=asyncio.subprocess.PIPE if stdin else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL if stdin else asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def _run(channel: Channel, argv: Sequence[str], *, stall: timedelta) -> _Ended:
    process = await asyncio.create_subprocess_exec(
        *channel.argv,
        *argv,
        env=dict(channel.env),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), _seconds(stall))
    except TimeoutError:
        process.kill()
        await process.wait()
        return _Ended(None, b"", b"")
    return _Ended(process.returncode, stdout, stderr)


async def _end(process: asyncio.subprocess.Process, errors: asyncio.Future[bytes]) -> None:
    """A process this module started, ended and waited on however its
    reader left it, so none outlives its call."""
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()
    if not errors.done():
        errors.cancel()
    elif not errors.cancelled():
        errors.exception()  # read, so an error of its own is never reported unread


def _seconds(stall: timedelta) -> float:
    return stall.total_seconds()
