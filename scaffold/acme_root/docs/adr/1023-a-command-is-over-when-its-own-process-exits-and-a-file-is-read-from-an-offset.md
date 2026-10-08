# ADR 1023: A command is over when its own process exits, and a file is read from an offset

**Status**: accepted (2026-10-07)

## Context

A command's output is a pipe, and the pipe stays open while any process
holds it. A command that starts a child and exits leaves the child
holding it. A transport that reads the output to its end then waits on
the child, up to the command's deadline, and a runner that serves one
command at a time is held all that while.

A caller that follows a file a command keeps writing, such as a long
run's log, polls it. A read that answers only the file's head makes each
poll read the whole file again, and the cost of a poll grows with the
file.

## Decision

**A command is over when its own process exits.** Its output is read on
for a short bound, two seconds, so what its children print as they
finish is kept. What still holds the output then is ended: each process
of the command's group, and each one descended from one, frozen as it is
found, walked again after each freeze, then killed. The command answers
its own exit code. Its pid is never signalled once it has exited, since
it may name another process by then; its group cannot while a process of
it lives. A process the command left that does not hold its output, such
as a server it started with its output elsewhere, runs on.

**In the account mode, the end runs as the account.** The account's own
view of `/proc` finds what to end, and a signal sent as the account
reaches the account's processes alone, whichever process a pid names by
then. It never ends every process of the account: the calls of one
response run concurrently in one workspace, and another command's
processes are the account's too.

**A file is read from an offset.** A read takes an offset and answers at
most its bound of the bytes after it, sought rather than read through,
and nothing at or past the file's end. On this host, the read walks the
path one step at a time and follows no link at any step, so a caller
that polls a path reads the file there and never one a link swapped in.

## Consequences

- A command that leaves a child holding its output answers within its
  drain and the grace that follows the end, about four seconds after
  its exit, never at its deadline.
- What a child would print after the drain is lost with it. A command
  that wants a child's whole output waits for the child.
- A process that left both the command's group and its tree before the
  end escapes it, and ends with the workspace's release.
- In a container, `docker exec` answers when the command's own process
  exits, and what the command left runs on until the release.
- A file in a host workspace is read at its own path, never through a
  link. A write and a listing there resolve a link inside the workspace.
