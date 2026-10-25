# ADR 1042: A session's title is content, sealed under a key a page opens at once

**Status**: accepted (2026-10-25)

## Context

A person names a session when they start it, and a model names each
sub-agent it spawns. The name sat in the clear on the session's row,
outside the seal, so a database reader read every title, and a revoked
key left each one behind. A title is content: the usage record already
leaves it out for that reason (ADR 1014).

A step's content is sealed under its session's data key, which the key
service unwraps. Sealed that way, a page of the session list would cost
one unwrap per session. On this stack's KMS emulator one unwrap takes
0.7 ms, and fifty, sent together, take 16 ms: the client's own work for
each call adds up, and its pool holds ten. Each is also a call the key
service bills and caps. Nothing may keep opened content, or a key in the
clear, between calls.

## Decision

**A title is content.** It is sealed at rest, plain in hand once opened,
and absent once the session's key is revoked. The rest of the session is
shape: its ids, its kind and tools, which are the registry's names, and
the causes the engine composes.

**A title is sealed under a key of its own, which a page opens at once.**
Each session's title key is made at random and kept on its privacy
record, wrapped under its tenant's title key with AES-GCM, bound to the
tenant and the session. The tenant's title key is a data key the key
service wraps, under an encryption context bound to the tenant and the
key, so another tenant's call unwraps nothing of it. A title is bound to
its tenant, its session, and its version, so one copied elsewhere opens
nothing. A page of titles takes one read of their title keys and one
unwrap of the tenant's, whatever its size. A title key opens no step.

**A read answers a title sealed, and what shows it opens it.** The views
of the API, the sub-agent tools, and a child's report to its parent open
the titles they show, a page at a time (`open_titles`). The tenant's
title key is in the clear only inside that call, or the one that seals a
title, and nothing keeps it after.

**The revocation takes the title key in its commit.** It clears the
session's title key in the commit that destroys every version of its
data key, so the title reads as absent at once, and the session keeps
its place. A rotation of the tenant's wrapping key rewraps the tenant's
title key with the data keys, and a tenant's purge takes it last.

**The title writes the session's privacy record, and chooses nothing.**
The record holds the default policy, not yet chosen. A choice before the
history begins takes it, and the history's first write fixes the
default, so a policy is still chosen once.

**An existing title is sealed in place, never dropped.** The migration
expands only (ADR 0084): the column the release before reads stays, and
each row this release writes holds the word `(sealed)` there, so either
release reads every row. The sweep seals each title stored in the clear,
a batch a pass, each in its own write conditioned on the version it
read, which it moves, so a writer that read the clear title before
cannot land it again. A revoked session's title goes, as its revocation
owed it. A pass that fails part way keeps what it sealed, and the rest
reads as it did, for the next pass. A later release drops the clear
column once both releases before are past this one.

## Consequences

- A database reader reads no title, and the left bar's page costs one
  call to the key service.
- The key service's log shows one unwrap per page of titles, not one per
  session: the tenant's title key opens every title of its tenant.
- A database backup taken before a revocation still holds that session's
  wrapped title key, as it holds its wrapped data keys. The revocation
  erases what the platform holds, not the copies its backups keep.
- A memory-only session's title is kept sealed at rest: it is written
  with the session, before a policy is chosen.
- Starting a session costs one read of its title key and one unwrap, and
  a tenant's first title one key made.
- An absent title reads as an empty string, as a step whose content is
  gone says nothing.
