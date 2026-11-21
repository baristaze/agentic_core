# ADR 1045: A VM workspace keeps its directory as sealed parts, and starts again on its base

**Status**: accepted (2026-11-20)

## Context

A VM's disk costs about ten times what its workspace's files do. It is
bound to the store of the host that keeps it, and it is encrypted at
rest under a key of the platform's, never sealed under a session's: a
revocation reaches it only through a sweep. Every other snapshot is
sealed under its session's key in the guideline's buckets
([ADR 1027](1027-a-workspace-is-snapshotted-whole-and-a-restore-a-rewind-or-a-fork-starts-from-it.md)),
so it is noise the moment the key goes.

A workspace's files are still too large to hold whole in memory, seal
whole, and put in a bucket as one blob at the end of every run.

## Decision

**The directory alone.** A VM workspace's snapshot keeps its directory,
`/workspace`, and nothing else. Each VM a session uses starts from the
workspace's base: its image, or its setup's disk
([ADR 1029](1029-a-vm-workspace-is-a-machine-of-its-own-and-its-base-is-a-disk-the-vms-keep.md)).
So what its commands installed outside the directory comes from the base
again, and the model is told so: a notice follows each restore of a
directory alone, and a rewind's or a fork's notice says it too.

**The provider packs; the tools keep.** A provider whose instances start
from their base says so (`keeps_directory`). It measures the directory by
the bytes its files hold, and refuses one past the bound the tools give
it before anything is packed, naming its size and the bound. Then it packs
the directory into one tar archive in the instance's temporary space, at
a path it names before tar starts, under a time bound of its own
(`vm_pack_timeout_seconds`, ten minutes by default): a pack that fails,
outlasts it, or is cancelled is removed, so none is left to fill the
instance's disk. It reads the pack out in parts, drops it, and restores a
pack's bytes into a new instance on the base. Where the parts go, and
what seals them, is the tools'. The packing runs through a channel (`packs`),
so a provider that reaches its instances by a command line on the
engine's host, a layer's own included, archives and restores a directory
the same way. It runs as the instance's root where its user may become
root without a password, so a file a command wrote as root comes back as
it was.

**Parts, sealed, under the session's names.** The tools give the
provider the bound (`directory_bound`, 4 GiB by default), and hold the
pack's own size to it before any byte of it is read, since an archive
holds more than its files. They
scan the pack for an injected secret's value, as they scan any snapshot.
Then each part (`snapshot_part_bytes`, 8 MiB by default) is sealed under
the session's key on its own, bound to its own keyed hash, and put at
`agent-sessions/<session>/workspace/<snapshot>/part-NNNN` under the
tenant's prefix. A manifest names each part by its hash and its size,
and is kept as any snapshot is, under its own keyed hash. The
`snapshotted` step names the manifest's hash, the archive's size, and
that it holds a directory. A store that fails part way removes every
part it put, so no step names it and the snapshot before stays the
latest. The pack goes from the instance however the snapshot ends.

**Restore.** The manifest is trusted by its hash before anything is
replaced. Then a new instance starts on the base, beside the one the
workspace stands on, its directory is emptied, and the parts are
unpacked into it in order, each trusted only once it opens under the
session's key and matches its hash. The instance it replaces goes only
once the new one holds the whole directory. A part gone, erased, or
altered loses the restore: the new instance goes, never left
half-restored, nothing kept is touched, the instance it would replace
stands as it was, and the loop parks for a person as a lost restore
parks. The person's unlock goes on from that instance's files. A VM
takes its workspace's names in turn, three of them, so the new VM
stands under the next while the old one stands; of two found, the later
in turn is a restore's that was cut off, and it is destroyed.

**Forks.** A fork's child keeps parts of its own, read from the pack the
spawn took and sealed under the child's key, so the parent's purge and
revocation never reach them.

**Privacy.** A session that keeps no content at rest keeps no archive:
its snapshot is refused before its directory is packed. Revoking the
session's key leaves every manifest and every part noise, and the
session's purge removes them with its other snapshots, since they sit
under its prefix. Nothing of a session's snapshot is kept outside the
seal, so a revocation has nothing more to erase.

**A base stays a disk.** A base's setup writes outside the directory,
so a VM's base is its setup's disk, kept by the VMs under the tenant
(ADR 1029). It holds no session's content, and the VMs' store, encrypted
at rest, stands for the buckets' encryption. A spec kept by snapshots no
longer needs that store; a base with setup still does.

## Consequences

- Every run of a VM workspace kept by snapshots starts on a new VM. On
  Lima that is a boot: seconds on a base's disk, and a minute or more on
  a bare image, which installs Docker at its first boot. A tool a
  session needs at every run belongs in its base.
- Each run's end packs and sends the whole directory, and no part is
  shared between archives, so removing a failed one never reaches an
  earlier one. An archive whose parts are mostly those of the one before
  costing more than its run is the trigger to share parts by their hash.
- With a secret the catalog may inject, the pack is read twice: once by
  the scan, once by the store.
- A file that a process left running changes while it is packed is kept
  as it was read, as GNU tar packs it.
- A restore holds two instances at once, for as long as the parts take
  to unpack: the host needs room for both.
- The pack needs the directory's size free in the instance's temporary
  space.
