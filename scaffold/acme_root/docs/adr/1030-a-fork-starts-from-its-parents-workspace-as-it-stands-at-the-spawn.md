# ADR 1030: A fork starts from its parent's workspace as it stands at the spawn

**Status**: accepted (2026-10-10)

## Context

ADR 1027 starts a forked sub-agent from its parent's latest snapshot. A
snapshot is taken only at a run's end, so a child spawned in the middle
of a run misses everything its parent wrote in that run. A fork in the
parent's first run is refused, since no snapshot exists yet, and a
parent whose workspace is a cache never holds one. A fork exists to try
something on what the parent has now. A child that starts on older
files works on them without knowing it.

This amends the fork of ADR 1027. Its restore, its rewind, and the
snapshot at a run's end stand as they are.

## Decision

**Taken at the spawn.** A spawn with `fork` takes its parent's
workspace as it stands, live, through the run that holds it: only that
run holds the instance, and the epoch its writes go under. The spawning
call's runtime takes it, as it runs a command. The broker takes back
every credential it attached there, the provider snapshots the
workspace, and the archive is scanned for the value of every secret the
catalog's tools may inject.

**What the parent keeps.** Its durability decides. A workspace kept by
snapshots keeps this one as its own too, named by a `snapshotted` step
in the call's loop, between the call and its answer. Like every such
step, it changes no status and opens no loop, and a rewind may name it.
A cache keeps nothing of it, no step and no bytes, so its durability
holds.

**The child's copy.** The archive stays in memory until the child is
made. It is then sealed under the child's key and stored as the
child's, and the child's restore names it before the objective that
wakes it, as in ADR 1027. Nothing is read back from the store: the
bytes the spawn took are the bytes kept.

**Refused before anything is spent.** The snapshot is taken after the
spawn's own checks, and before the tree's slot is taken or the child is
made. A refusal is the call's failure, with its reason: a provider that
cannot snapshot, a workspace that holds an injected secret's value, or a
session that keeps no content at rest. The last is asked before
anything is taken. A child keeps its content at rest by default, so a
copy would otherwise put a memory-only parent's workspace at rest.

**Asked again.** A spawn asked again under the same id answers the child
it made. When the child's history already names its copy, nothing is
taken again. Otherwise the run that asks takes the workspace as it
stands then.

**Its time.** A fork pauses, exports, and stores a container before the
child is made. A spawn's timeout is ten minutes, so a workspace of some
size forks.

## Consequences

- A fork needs no earlier run and no `snapshot` durability. Any
  workspace its provider can snapshot forks.
- A fork of a parent kept by snapshots stores two copies, the parent's
  and the child's, each sealed under its own key. A cache parent stores
  one.
- A fork pauses the parent's container while the snapshot is taken, and
  holds the archive in memory until the child's copy is stored. ADR
  1027's trigger for a streamed put holds here too.
- A session that keeps no content at rest cannot fork.
