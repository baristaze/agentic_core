# ADR 1035: A loop's end and its park name their cause, composed from what the spec declares

**Status**: accepted (2026-10-12)

## Context

A loop whose workspace cannot be prepared stops before its first model
call. A refusal that never clears ends it `errored`. One that clears
parks it on the resource, and a lost workspace parks it for a person.
The `loop_ended` step holds its outcome alone, and a park its reason,
its unlock, and its retry time. Why the workspace was refused, such as
the base's setup command that failed and its exit code, reaches the
runner's log and nothing a person reads. A person who reads the session
sees `errored`, or a wait, with no cause, and only the operator can say
what to fix.

The refusal's message cannot simply be shown. It is the log's, and it
holds what a reader of a session must not read: what Docker answered,
what a pull wrote, the host's accounts, its kernel's settings, a path on
it, another workspace on the same account.

## Decision

**A cause beside the outcome and the reason.** `LoopEndedHeader` and
`Park` take an optional `cause`: why the loop ended or parked, in words
a person reads. It is optional because most ends and parks say enough
by their outcome or their reason. The API returns it on the step, as a
`loop_ended` step's `cause` and a `parked` step's `park.cause`, and on
the session's park. The CLI's history line adds it after the outcome or
the park.

**Composed from what the spec declares.** An `IsolationRefused` carries
a `cause` beside its message. A provider sets it only when its why is
the spec's: a mode, an egress, or a limit it cannot meet, or a snapshot
it cannot keep. The engine sets it on its own refusals: a base being
built, a base built again, a base that holds a secret's value, and a
setup command that failed, named by its place, its text, and how it
ended. A refusal whose why is the host's own state names none, and its
loop's end or park says its outcome or its reason alone. A cause never
holds a command's output or an environment.

**A lost workspace in the engine's words.** The message of a
`AgentWorkspaceLost` may quote a pull's answer or a store's error, so the
engine names the cause itself: the snapshot the workspace starts from
is gone, or no longer matches what was kept; or what the workspace is
rebuilt from is gone, or cannot be brought in.

**In the header.** A cause is shape, not content. A setup command is
declared with its kind's base, which is the tenant's and never a
session's ([ADR 1028](1028-a-workspace-base-is-built-once-for-its-tenant-and-kept-as-a-snapshot.md)),
and a cause names nothing a session said or a tool returned. So it sits
in the header, which stays readable when a session's key is revoked, and
in the session's cached park. A cause past 500 characters, such as a
long setup command, is cut with an ellipsis, never refused, so the step
that ends or parks the loop is always written.

## Consequences

- A person reads why a loop stopped from the API alone, and fixes a
  setup command, a spec, or a base without the operator's logs.
- A provider's new refusal names no cause until its author composes one
  from the spec. A missing cause leaves a person less to read, never
  more than they may.
- An end that names no cause yet, such as a provider's error or a model
  role no fill resolves, can name one through the same field.
- Nothing is migrated: a step's header and a session's park are stored
  as JSON, and both read an absent cause as none.
