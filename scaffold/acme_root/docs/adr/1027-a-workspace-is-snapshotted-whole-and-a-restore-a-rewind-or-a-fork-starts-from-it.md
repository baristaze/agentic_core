# ADR 1027: A workspace is snapshotted whole, and a restore, a rewind, or a fork starts from it

**Status**: accepted (2026-10-10)

## Context

A workspace is a cache. A release lets its instance go and keeps its
files; a container keeps its volume and loses its writable layer, so a
tool a command installed outside `/workspace` is gone at the next loop.
A session that must resume on its whole machine, a local database or
the packages it installed, has nothing to resume from, and a sub-agent
that should try a change on a copy of its parent's workspace has no copy
to start from.

The engine owns the providers and the history; it does not own the
lifecycle around them. When to snapshot, where a snapshot is stored
relative to a customer's wall, and how long it is kept are a platform's.
So the engine states the capability, and a platform calls it.

## Decision

**The provider's seam.** A workspace provider snapshots a live
workspace into one archive of its own format, and its `prepare` takes
an optional archive and starts the workspace from it, replaced whole.
The archive passes as bytes, since the guideline's buckets take bytes; a
VM's disk goes through the same two calls.

**Who can.** A directory on a host refuses, and so does an account's:
their commands write outside the directory, so a snapshot of it would
lose what they installed without a word. The refusal comes before
anything runs. A spec may ask for a workspace that can be snapshotted
(`durability: snapshot`); a provider that cannot refuses that spec at
prepare, before the first model call, never a cache in its stead.

**The container's snapshot.** It holds the container's whole
filesystem: what `docker diff` names in its writable layer, read out of
a streamed `docker export`, the paths removed from the image, and the
volume, read with `docker cp`, with the id of the image beneath. It is
taken with the container paused, so nothing writes while it is. A
restore starts a container on that image, by id, never by a tag that
may have moved, copies both archives back with owners and modes kept,
and removes the removed paths again. What Docker writes into every
container, its hosts file and its init, is never kept. A container's
hostname is its name, so a restored one answers to the same. A restore
that fails part way removes what it made.

A commit of the container was the other way. It leaves an image in the
daemon to name and remove, and saving it carries every layer of the
image beneath, so each snapshot would hold the whole image again. The
filtered export holds only what the commands wrote. Its cost is that a
restore needs the image by its id: one that is gone loses the workspace,
loudly, and the loop parks for a person.

**Where it is kept.** In the guideline's buckets, a bucket of its own,
`snapshots`, under `agent-sessions/<session>/<hash>`: the hash of its
bytes keyed by the session, as a call's input hash is. It is sealed
under the session's key, bound to the session and the hash, so revoking
the key erases it, and a session that keeps no content at rest keeps no
snapshot. A `snapshotted` step names it: its id, its hash, its size, and
the workspace it came from. The step changes no session's status and
opens no loop, since a platform may take a snapshot after a loop ends.

**No credential.** Before a snapshot, the broker takes back everything
it attached in the workspace, what a lost run never took back included.
The archive is then scanned for the values of every secret a tool of the
catalog may have injected, in each form redaction matches, and one that
holds a value is refused with the secret's name: nothing is kept.

**Restore, rewind, fork.** A `restore` control names a snapshot the
session's history holds. The loop's next prepare loads it, opens it,
and holds it to its hash before the provider sees it: bytes gone,
erased, or altered lose the workspace before it starts. An
`environment_changed` step that references the control records the
restore and tells the model. A principal's restore is a rewind; nothing
is deleted. A spawn with `fork` copies its parent's latest snapshot,
sealed again under the child's key and stored as the child's, and writes
the child's restore before its objective, so the child's first loop
starts from it and its writes never reach the parent.

**Purge.** A snapshot is content. A session's purge removes every
snapshot under its prefix, with its workspace and its records.

## Consequences

- A session's snapshots are its own: a child's copy outlives its
  parent's purge and its parent's revoked key.
- A restore runs on the image the snapshot was taken on. A host that
  pruned it loses the workspace and parks the loop, as for any lost
  durable state.
- A snapshot is held in memory whole while it is taken, sealed, and
  stored. A workspace whose archive outgrows a worker's memory is the
  trigger for a streamed put.
- A workspace whose files hold a secret's value keeps no snapshot until
  the value is gone.
