# ADR 1027: A workspace is snapshotted whole, and a restore, a rewind, or a fork starts from it

**Status**: accepted (2026-10-10), amended by [ADR 1030](1030-a-fork-starts-from-its-parents-workspace-as-it-stands-at-the-spawn.md)

## Context

A workspace is a cache. A release lets its instance go and keeps its
files; a container keeps its volume and loses its writable layer, so a
tool a command installed outside `/workspace` is gone at the next loop.
A session that must resume on its whole machine, a local database or
the packages it installed, has nothing to resume from, and a sub-agent
that should try a change on a copy of its parent's workspace has no copy
to start from.

The engine owns the providers, the runs, and the history; it does not
own the lifecycle around them. Which workspaces are kept by snapshots,
where a snapshot is stored relative to a customer's wall, and how long
it is kept are a platform's. When to take one is not: only the run that
holds a workspace holds its live instance, and a release takes the
container's writable layer with it. So the engine states the capability
and takes the snapshots, and a platform chooses the durability.

## Decision

**The provider's seam.** A workspace provider snapshots a live
workspace into one archive of its own format, and its `prepare` takes
an optional archive and starts the workspace from it, replaced whole.
The archive passes as bytes, since the guideline's buckets take bytes; a
VM's disk goes through the same two calls.

**When it is taken.** A workspace of durability `snapshot` is
snapshotted by the run that holds it, live, at the run's end: before
its loop parks, ends, or yields, so no run that follows starts before
the step names it. Its instance goes only once a snapshot holds it. A
snapshot that is refused, as for a secret's value in the workspace, or
that fails is logged, and the instance stays, so nothing in it is lost.
The next run starts from the latest snapshot when nothing touched the
workspace after it. A tool call after it, as a run lost before its end
leaves, or a change an `environment_changed` step records, a person's
work by hand or a lost restore, leaves the workspace newer than any
snapshot, so it is prepared as it stands. A pending restore wins over
both. A released workspace holds no instance, so a snapshot asked of it
is refused, and no volume is ever kept as the whole.

**Who can.** A directory on a host refuses, and so does an account's:
their commands write outside the directory, so a snapshot of it would
lose what they installed without a word. The refusal comes before
anything runs. A spec may ask for a workspace that can be snapshotted
(`durability: snapshot`); a provider that cannot refuses that spec at
prepare, before the first model call, never a cache in its stead.

**The container's snapshot.** It holds the container's whole
filesystem: what `docker diff` names in its writable layer, read out of
a streamed `docker export`, the paths removed from the image, and the
volume, read with `docker cp`. It names the image beneath by its id and
by the digest a registry serves it under, when one does. It is taken
with the container paused, so nothing writes while it is. A restore
starts a container on that image, by id, never by a tag that may have
moved: a host that lacks it pulls it by the digest and holds it to the
id. It copies both archives back with owners and modes kept, and
removes the removed paths again. What Docker writes into every
container, its hosts file and its init, is never kept. A container's
hostname is its name, so a restored one answers to the same. A restore
that fails part way removes what it made.

A commit of the container was the other way. It leaves an image in the
daemon to name and remove, and saving it carries every layer of the
image beneath, so each snapshot would hold the whole image again. The
filtered export holds only what the commands wrote. Its cost is that a
restore needs the image: one no registry serves, a pull that fails, or a
pull that brings another image loses the workspace, with the reason,
before anything of it is removed, and the loop parks for a person.

**Where it is kept.** In the guideline's buckets, a bucket of its own,
`snapshots`, under `agent-sessions/<session>/<hash>`: the hash of its
bytes keyed by the session, as a call's input hash is. It is sealed
under the session's key, bound to the session and the hash, so revoking
the key erases it, and a session that keeps no content at rest keeps no
snapshot. A `snapshotted` step names it: its id, its hash, its size, and
the workspace it came from. The step changes no session's status and
opens no loop: it is written as a run ends, and says nothing of what
the loop did.

**No credential.** Before a snapshot, the broker takes back everything
it attached in the workspace, what a lost run never took back included.
The archive is then scanned for the values of every secret a tool of the
catalog may have injected, in each form redaction matches, and one that
holds a value is refused with the secret's name: nothing is kept. The
engine holds those values, and only those: a brokered credential never
reaches it, so it is detached, never scanned for.

**Restore, rewind, fork.** A `restore` control names a snapshot the
session's history holds. The loop's next prepare loads it, opens it,
and holds it to its hash before the provider sees it: bytes gone,
erased, or altered lose the workspace before it starts. An
`environment_changed` step that references the control records the
restore and tells the model. A principal's restore is a rewind; nothing
is deleted. A spawn with `fork` reads its parent's latest snapshot
before it takes the tree's slot or makes the child, and one whose parent
holds none is refused with nothing spent. It copies the snapshot, sealed
again under the child's key and stored as the child's, and writes the
child's restore before its objective, so the child's first loop starts
from it and its writes never reach the parent.

**A lost restore.** A restore that cannot load, a rewind's, a fork's, or
the next run's from the latest snapshot, parks the loop for a person.
The person's unlock tries it once more, then goes on from the workspace
as it stands: what the provider holds without a snapshot, for a
container its volume on a fresh instance. No older snapshot is tried, so
the loop goes on from a state the person can see, and a rewind to one is
theirs to ask. An `environment_changed` step that references what named
the snapshot records the restore lost: it is never tried again, and the
model reads it.

**Purge.** A snapshot is content. A session's purge removes every
snapshot under its prefix, with its workspace and its records.

## Consequences

- A session's snapshots are its own: a child's copy outlives its
  parent's purge and its parent's revoked key.
- A restore runs on the image the snapshot was taken on, pulled by its
  digest where a host lacks it. An image no registry serves, such as
  one built on the host, cannot move between hosts with its snapshot.
- Hosts that restore one another's snapshots share an architecture: a
  digest names an image for every platform, and a host pulls its own. A
  pool that mixes architectures is the trigger to name the platform in
  the snapshot.
- Each run of a `snapshot` workspace ends with a pause, an export, and a
  store; a `cache` workspace pays none of it.
- A snapshot is held in memory whole while it is taken, sealed, and
  stored. A workspace whose archive outgrows a worker's memory is the
  trigger for a streamed put.
- A workspace whose files hold a secret's value keeps no snapshot until
  the value is gone, and keeps its instance live until then.
