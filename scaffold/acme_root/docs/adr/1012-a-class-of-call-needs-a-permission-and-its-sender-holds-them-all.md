# ADR 1012: A class of call needs a permission, and its sender holds them all

**Status**: accepted (2026-10-02)

## Context

A message to a session enqueues its loop. The guideline's work queue
authorizes work once, at enqueue: the permission that enqueues a kind
covers every call its handler makes. The engine applies that rule to a
session's registry. A principal may start or instruct a session only if
it may make each kind of call the registry offers. Policy then gates each
call on its own.

Nothing said which calls a principal may make. A tool names its class,
the kind of power it uses, and a tenant grants permissions, not classes.
So a member could message a steady session an admin started, and its
agent would change the tenant's configuration under the admin's
authority, on the member's word.

## Decision

**Each class needs one tenant permission.** `read` needs `read`.
`configuration` needs `manage_members`, the permission that writes the
tenant's own configuration, its tool policy among it. `credentials`
needs `manage_keys`, the permission that manages its keys. Every other
class needs `write`, a domain class an adopter declares included. The
table is `CLASS_PERMISSIONS` in `om/src/acme/om/tools/rules.py`.

**The sender holds every permission its session's registry needs.** A
start is refused before anything is made, and a principal's message is
refused at the inbox, with nothing appended. An agent's message, an
event from outside, and a control are no principal's instruction, and
are not asked.

**The agents answer it.** A session's registry is its kind's tools that
the catalog holds, so the agents manager reads it; the root binds the
inbox's check to it.

## Consequences

- A message buys no call its sender may not make, in either authority
  mode.
- A viewer, who holds `read` alone, instructs only a session whose
  tools read.
- An adopter whose class needs more than `write` adds its row to the
  table.
- A spawn is not asked: a child's registry is cut to its parent's, whose
  sender was asked already.
