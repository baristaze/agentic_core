# ADR 1010: A history is purged by a login of its own

**Status**: accepted (2026-10-02)

## Context

A session has three deletes. Mark deleted hides it and can be undone.
Revoking its key destroys its content and keeps its shape. Purge
removes its rows after the shape's retention, and it is the guideline's
one hard delete, never an on-demand one.

The history is written once: the serving logins hold SELECT and INSERT
on `activity.steps` and nothing more
([ADR 1002](1002-the-history-is-written-once-and-a-stale-run-is-refused.md)).
So no login the guideline names can purge it. The guideline names three
logins (STO-28): the migration login, which owns the tables and runs in
the deploy's one-off task alone; the runtime login; and the system
login, its twin for the system scope. Giving either serving login
DELETE back would let any statement a request sends take a step out of
a history. A deleted tenant's history would stay in place otherwise,
and so would a session's past its retention.

A session's mark and its history live in two roles, `core` and
`activity`, and nothing crosses a role. So a purge is a series of
transactions, and an unmark can land between them.

## Decision

**A fourth login, `acme_purge`, deletes a history.** It holds SELECT and
DELETE on `agent_sessions`, `steps`, and `step_cursors`
(`PURGED_TABLES`), usage on their schemas, and nothing else: no INSERT,
no UPDATE, no other table, no ownership, no superuser, no `BYPASSRLS`.
The migrations that admit it grant it, and `ensure-logins` grants it
again on every run, as it takes the append-only table's rewrite back.

**The tenant fence admits it within one tenant.** Each table's policy
compares `org_id` with the transaction's tenant for every login, and
admits the system scope to the system login alone. So the purge login
reaches the tenant its transaction names and no row under the system
scope. The storage funnel refuses the system scope for it before any
connection opens.

**Only the maintenance worker holds it.** Its URL,
`ACME_DATABASE_PURGE_URL`, reaches the worker and the migrate task, which
sets the login's password from it, and no other process. It has no
default, so no process ever holds a known password for it: the worker
and `ensure-logins` refuse to start without it. A root built without it
deletes no step. The worker opens one connection for it, since the sweep
sends one purge at a time.

**A session's retention counts from its mark.** A session marked deleted
is hidden from every read, its history and its shape kept, and comes
back when unmarked. Once marked longer ago than the retention, 30 days
by default, the sweep's purge across tenants takes it up. It first
claims the session, a compare-and-set on its version that stamps
`purge_started_at`. An unmark that lands first keeps the session, and
one that comes after finds the claim and is refused: the delete is
final from the claim on. Then the history goes, a batch of steps at a
time and the cursor row last, and then the session's row. A failure
between two steps leaves a claimed session that the next pass resumes.
A session that was never marked, or was marked within its retention, is
never taken.

**A deleted tenant takes the same login.** The sweep's purge of a
tenant past its retention deletes its steps, its cursor rows, and its
sessions under the purge login, and marks the tenant purged only once a
pass finds none of them left.

## Consequences

- No statement a serving process sends deletes a step. The one that
  deletes one runs in the worker, under a tenant it names.
- This departs from STO-28, which names three logins. The fourth widens
  nothing the three hold, and each guarantee STO-28 states still holds
  and is tested: no login carries `BYPASSRLS`, and the system scope is
  the system login's alone.
- A deploy carries a fifth database secret, and `ensure-logins` makes
  four logins. A copy that points its URLs at a database of its own
  points the purge URL there too.
- A session whose content must go at once is revoked, not purged: the
  purge waits out the shape's retention.
