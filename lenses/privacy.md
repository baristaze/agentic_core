# Privacy

Group id: `privacy`. Covers Privacy and Null Objects of
`agentic_core_spec.md`.

This group judges what is sealed and how it is destroyed, and what
stands in for a dependency nobody provided: the session's key, content
and shape, the three deletes, storage modes, null objects, and the
contract cases every impl passes. It leaves a secret in a tool's process
to `tools`, the fills eligible for zero data retention to `models`, and
the shape a span carries to `trust`.

## PRV-01 Content is sealed per session, by envelope

**Principle.** Every step's content is sealed at rest under its
session's key, by envelope: a data key per session, wrapped by a key the
tenant's key service holds. A key per tenant or per user is too coarse;
a session is the unit people share, export, and delete. In memory,
content is plain. A decorator over step storage, behind the same
interface, seals content on its way in and opens it on its way out, and
the rest of the engine never sees it. A database reader sees
ciphertext. Opening content takes the session's key, which takes a
permission of its own.

**Source.** Privacy, A Key per Session.

**Look for.** The sealing decorator and where it is wired; the key a
step is sealed under; the permission that opens a session's key.

**Violation.** Content written in the clear, or sealed under a key per
tenant; sealing done in the engine's own code instead of a decorator
over step storage; a read that opens content without the key's
permission.

**Severity.** medium

**Check.** review

## PRV-02 A session's key has versions, and rotation touches no content

**Principle.** A session's key has versions. New content is sealed under
the current version, each sealed blob names its version, and old blobs
are never rewritten. Rotating the tenant's wrapping key re-wraps the
data keys and touches no content. The key service creates, wraps,
unwraps, and destroys a session's data keys; a memory impl serves a
process and a test, and a cloud key service a fleet. The key service has
no quiet null.

**Source.** Privacy, A Key per Session; Null Objects.

**Look for.** The version a sealed blob carries; the rotation path; the
key service's impls.

**Violation.** A sealed blob with no version; a rotation that re-seals
content; a null key service that passes content through unsealed.

**Severity.** medium

**Check.** review

## PRV-03 Content is sealed, and shape stays readable

**Principle.** Content is sealed: messages, events, tool inputs and
outputs, thinking, summaries, the plan, attachments and artifacts, and
anything derived, such as caches, indexes, and embeddings. Shape stays
readable: ids, types, sequence numbers, actors, origins, references,
times, sizes, stop reasons, failure classes, usage, cost, budgets, that
an approval happened and who decided, tool names, classes, effects, and
keyed hashes. A hash in the shape is keyed by the session. Telemetry,
logs, and traces carry shape only; redaction is no licence to log
content. Billing, audit, and tracing keep working on a session whose
content is gone.

**Source.** Privacy, Content and Shape.

**Look for.** Each persisted field and which side it sits on; derived
stores; hashes and their keys; what logs, metrics, and spans carry.

**Violation.** A derived index or embedding stored unsealed; an unkeyed
hash of content; content in a log line or a span, redacted or not;
billing or audit that reads content.

**Severity.** medium

**Check.** review

## PRV-04 Three deletes: hide, revoke, purge

**Principle.** Mark deleted hides the session and can be undone.
Revoking the key destroys every version of the session's key: the
content becomes noise and the shape stays, every step keeping its place,
its type, and its cost. It is the erasure of a session's content, in
place of the guideline's redaction of named fields. Purge removes the
shape's rows after the shape's own retention; it is the guideline's one
hard delete, never an on-demand one. Revoking a key destroys what the
platform holds under it; copies that left it follow their own
lifetimes.

**Source.** Privacy, Three Deletes.

**Look for.** The three delete paths and what each touches; what a
revoke leaves of each step; what can start a purge.

**Violation.** A revoke that deletes steps or rewrites content; a key
version a revoke leaves behind; a purge on demand; a mark deleted that
cannot be undone.

**Severity.** medium

**Check.** review

## PRV-05 A session's storage mode is chosen per session

**Principle.** Where a session must keep no content at rest, its storage
policy is chosen per session, by policy: sealed by default, or
memory-only, where content lives only while a runtime holds it and the
shape may still be persisted, when policy allows, so cost and audit
survive. Both impls are wired at boot, and a decorator routes each
session by its policy. A memory-only session keeps its runtime while it
is parked, up to a declared time; past it, or on a crash, its loop ends
`errored` and its shape stays. Zero data retention's engine half is a
memory-only session.

**Source.** Privacy, Storage Modes and Retention.

**Look for.** Where the storage policy is set and read; the routing
decorator; a memory-only session's park and crash paths.

**Violation.** A storage mode fixed for a whole process, so sessions
under different policies cannot differ; content of a memory-only session
written to any store; a memory-only session that stays parked past its
declared time.

**Severity.** medium

**Check.** review

## PRV-06 Every interface has an impl, wired by a root

**Principle.** Every dependency is an interface, and every interface
has an impl even when nobody provided one: a null object, which a root
wires like any impl, never a constructor default. The engine never asks
whether a dependency is there. The null object is the engine's addition
beside the guideline's in-memory impl and deterministic twin.

**Source.** Null Objects.

**Look for.** Constructor parameters that default to nothing or to a
null object; checks for a missing dependency in engine code; where each
null object is wired.

**Violation.** A dependency that defaults to `None` or to a null impl in
a constructor; a branch on whether a provider is `None`; a null object
built inside the engine.

**Severity.** medium

**Check.** review

## PRV-07 A null object may do nothing; it never pretends it did

**Principle.** An observer, a meter, a gate, or a sink may be quiet:
all that is lost is a number, and a lost verdict is marked, as the null
result gate marks a result unverified. Each quiet null is a declared
degraded answer. A capability that acts on the world is loud: its null
object refuses with a typed error the model reads, in one of the
guideline's exception shapes. A session with no workspace has a
transport that refuses every call, loudly.

**Source.** Null Objects; The Runtime.

**Look for.** Each null object, and whether it is quiet or loud; what a
quiet one reports; what a loud one raises.

**Violation.** A null transport or another acting capability that
returns success; a quiet null that returns a verdict unmarked; a loud
null that raises an untyped error.

**Severity.** medium

**Check.** review

## PRV-08 A root refuses a quiet gate or ledger outside local

**Principle.** A root refuses a quiet null budget gate or ledger at boot
outside `local`, by the guideline's rule on what a process refuses.

**Source.** Null Objects.

**Look for.** The root's checks at boot and the stage they read; which
null objects a root outside `local` accepts.

**Violation.** A root outside `local` that boots with the budget gate's
null object or a null ledger, so every model call spends unchecked.

**Severity.** high

**Check.** review

## PRV-09 Every impl passes the conformance kit

**Principle.** A conformance kit holds the contract cases every impl of
an engine interface must pass: step storage, the key service, the
ledger, the outage signal, the transport, the workspace provider, and a
provider adapter. An adopter imports it. A fake clock and a
deterministic id source make recovery, deadlines, and parking testable.

**Source.** Testing and Conformance.

**Look for.** The contract cases per interface; which impls run them;
the clock and the id source the engine's tests use.

**Violation.** An impl of an engine interface with no contract cases run
against it; a test of recovery, a deadline, or a park that reads the
wall clock.

**Severity.** medium

**Check.** review
