# ADR 1002: The history is written once, and a stale run is refused

**Status**: accepted (2026-10-02)

## Context

A session's history is every step it recorded. It is the source of
truth: the context a model reads, a session's status, its cost, and the
replay after a crash are all read off it. So a step that is rewritten,
removed from the middle, numbered twice, or written by a run that lost
its claim corrupts everything above it.

The guideline's fences hold the work row, never the record. A worker
that holds an item past its lease is refused at the work row, but two
runs of one model call diverge, and the first may still be writing.

The scaffold's serving logins hold every DML statement on every table,
by the default privileges of each role's first migration and again by
the login command a deploy runs before every migration.

## Decision

**A step is written once, and the database holds it.** The serving logins
keep SELECT and INSERT on `activity.steps` and lose UPDATE and DELETE.
The migration that creates the table takes the two back from the role's
default privileges, and the login command takes them back after each
grant it makes, for every table in `APPEND_ONLY_TABLES`. The storage
interface has no update and no delete.

**The numbers come from a cursor row.** One row per session holds the
last `seq` and the writer epoch. An append takes `head + n` for its `n`
new steps under the row's lock, in the statement that writes them, so a
history is gapless and a rollback returns its numbers. It is idempotent
on the step's id.

**A run takes an epoch, and every append it makes names it.** A run moves
the row's epoch one up when it begins, before it reads the history. Its
appends change the row only while it still holds that epoch, so a run
that lost its claim takes no number and writes nothing (`StaleWriter`).
The wait on the row's lock reads the row again, so a run that begins
while an append waits fences that append too.

**Inputs and controls arrive without an epoch.** They come whether or not
a run holds the session, so the inbox's append takes no epoch, and takes
inputs and controls alone. What the engine writes goes through a run.

## Consequences

- No statement a process sends rewrites a step or opens a gap in a
  history, whatever reaches it.
- The purge of a history, the one delete it has, needs a path of its own
  that the serving logins do not hold.
- The epoch fences the record only. A transport that runs a run's
  commands checks the epoch they carry on its own side.
