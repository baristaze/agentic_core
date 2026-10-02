# ADR 1004: A session's content is erased by revoking its key

**Status**: accepted (2026-10-02)

## Context

STO-32: "Personal data lives in named fields, so erasing a person is a
sweep over a list, not a hunt."

STO-34: "Outbox payloads and events, an audit entry among them, carry
ids, never the values of personal fields, so erasing a person never has
to rewrite them. [...] An append-only record that holds a personal field
has it redacted in place by the erasure sweep, the one write such a
record takes, and the sweep states that write."

A step's content is whatever people type and tools return: free text,
documents, a model's thinking. It holds personal values, and stray
secrets, in no field anyone can name, because the model must read them.
There is no list to sweep and no field to redact. The history is written
once, and no serving login may rewrite a step
([ADR 1002](1002-the-history-is-written-once-and-a-stale-run-is-refused.md)),
so a redaction in place is a write the database refuses.

A session is the unit people share, export, and delete.

## Decision

**Content is sealed per session, and its erasure is the revocation of the
session's key.** Every step's content is sealed at rest under its
session's key, by envelope: a data key per version, kept in
`core.session_keys` wrapped by the tenant's key service, never in the
clear. A layer under step storage seals on the way in and opens on the
way out. Revoking the key empties the wrapped copy of every version in
one commit, and keeps each row, so the record says which versions
existed and when they were destroyed. The steps are not touched: each
keeps its place, its type, and its header, and its content reads as
absent. This is the deviation from both rules.

**The key service keeps no copy of a data key.** The platform keeps the
one wrapped copy, so destroying it is destroying the key: the cloud key
service can unwrap only what it is handed. Using the tenant's key is the
task roles' permission, granted on the environment's keys alone; a
database login, or a role that reads the database, holds wrapped keys and
cannot open one. In the cloud that key is the account's, outside every
environment's graph, so destroying an environment leaves its backups
openable, and the key goes only by hand.

**Shape stays readable.** Ids, types, sequence numbers, references,
headers, and hashes keyed by the session are not sealed, so billing,
audit, and tracing keep working on a session whose content is gone. A
keyed hash is derived from the session's first version, and goes with
it.

## Consequences

- A revoked session takes no content again; a step that says nothing
  still lands, so a loop under way can end.
- A copy of a wrapped key in a database backup outlives the revocation
  until the backup ages out. Copies that left the platform follow their
  own lifetimes.
- The purge after the shape's retention stays the one hard delete of a
  history's rows.
- Every append that carries content and every read of sealed content
  unwraps a key: one call to the key service each.
