# ADR 1029: A VM workspace is a machine of its own, and its snapshot is its disk

**Status**: accepted (2026-10-10)

## Context

The isolation spec has a VM mode, and no provider serves it. A long
agent's work often needs a box of its own that runs containers: an
engineer that builds and tests an image. Containers do not nest safely;
a virtual machine does.

A snapshot passes as one archive of bytes
([ADR 1027](1027-a-workspace-is-snapshotted-whole-and-a-restore-a-rewind-or-a-fork-starts-from-it.md)).
A VM's disk is gigabytes. Held in memory, sealed, and put in a bucket at
the end of every run, it would cost more than the run.

## Decision

**Machines, a capability.** The engine's infrastructure gains
`machines`, in the shape of the guideline's buckets: an interface, an
implementation per backend, a twin, and a setting that picks one. It
starts a machine from an image or a snapshot, runs a command in it,
stops it, snapshots its disk, and destroys it, each by a name its caller
chooses. It says up front whether its host has a hypervisor (its
`probe`), the egress it closes from outside a guest, the limits it
enforces, and whether the store of its snapshots is encrypted at rest.
Nothing in it is local but its channel: the command line on the
engine's host that reaches a machine, as an SSH client reaches a VM in
a cloud. A cloud's implementation is the platform's, behind the same
interface. With no backend set, every VM workspace is refused.

**Lima, locally.** Lima runs each machine on this host's hypervisor,
Apple's virtualization on macOS and KVM on Linux. A machine is made
from a template the engine writes, on the image's own Lima template. It
mounts no host directory, forwards no port to the host, takes none of
the host's proxy variables or keys, and forwards no agent. Docker is
installed from the distribution's packages at its first boot, its
socket the guest user's, and a start waits until Docker answers. The
`limactl` command line runs with the path, the home, and Lima's own
variables alone, so nothing of the engine's environment reaches a
guest. A deployed process refuses Lima at boot.

**One machine per workspace.** The VM provider names a workspace's
machine by its id, under a prefix the operator sets. Its folder is
`/workspace`, where commands start and which is their home. Its
commands hold the guest whole, its root through Docker and `sudo`: the
machine is the wall, so a base's setup holds nothing more than a
session's commands do
([ADR 1028](1028-a-workspace-base-is-built-once-for-its-tenant-and-kept-as-a-snapshot.md)).
A release stops the machine and keeps its disk; the next prepare starts
it again. One found under other cpus or memory is stopped and started to
the new spec, its disk kept. Before a machine starts, the provider refuses what
its machines cannot hold: an allowlist, a process limit, part of a cpu,
an egress they cannot close, and a snapshot or a base with setup where
their store is not encrypted at rest. Then it probes, and a host with no
hypervisor refuses the workspace, naming why, before any command.

**Egress.** A Lima guest reaches out through a network the host runs
in user space. Nothing outside the guest closes it, and a rule inside
would be undone by the guest's root, which any command that runs
Docker has. So Lima closes no egress: a workspace that must reach
nothing is refused before a machine starts, and one with open egress
runs as a container's does. The guest's gateway also answers for this
host's own loopback, as a desktop's Docker answers for its host. What
the host serves there is the host's to close
([ADR 1017](1017-a-wall-is-checked-at-each-call-and-a-run-reads-what-is-new.md)),
and it is why a deployed process refuses Lima.

**Commands.** The VM transport reaches a machine through the channel
its machines give. A command's variables and its injected secrets go on
its standard input, in base64, to a launcher inside, never on a command
line another process lists. The launcher writes the command's pid. At
the deadline the command line ends, then the command's tree inside, by
that pid. Records and the epoch fence stay on the host, as a
container's do.

**The snapshot is the disk.** A snapshot stops the machine, so nothing
writes, keeps its disk as a snapshot of the machines, and starts the
machine again. Lima keeps it as a stopped instance cloned from the
machine, a copy-on-write clone of the disk file where the filesystem
clones files. Its digest is a SHA-256 of each run of data the disk
holds, by offset, and of its size, so a sparse disk's holes are never
read. The archive the history names holds the tenant, the snapshot's
name, and that digest: a few hundred bytes, sealed and kept as any
archive is. A restore holds the archive to its hash, then the disk to
its digest and its tenant, before the machine it replaces goes. A disk
gone or altered loses the workspace, and the loop parks for a person.

**What stands for the seal.** A disk is never sealed under the
session's key: encrypting gigabytes per session at each run's end is
the cost this decision avoids. The machines' store stands for the seal.
It is encrypted at rest: locally, the disk that holds Lima's instances,
under FileVault or LUKS, which the operator declares
(`machine_store_encrypted`); in a cloud, its disk snapshots under its
keys. Revoking a session's key erases every archive, so no restore names
a disk again, and the disk goes with the purge.

**Purge, forks, and bases.** A purge destroys a workspace's machine and
every snapshot kept under its name. A copy that must outlive the
workspace it came from is kept under its own: the provider interface's
`keep` copies a snapshot under another workspace. A fork keeps its copy
under the child's workspace, so the parent's purge leaves it. A base is
kept under an id of its own, taken from the tenant and the base's key,
so the build's purge leaves it; it goes when the base is dropped and
with the tenant's purge. An archive that holds its own bytes, a
container's, is its own copy, and `keep` answers it as it is.

**No credential.** The provider interface's `held` reads what a
snapshot holds, for the scan for injected secrets' values: the archive
itself for a container, the disk's data for a VM, streamed with a
holdback across parts. With no secret to look for, nothing is read.

## Consequences

- A VM's snapshot stops it for seconds: on this kind of host, two to
  stop, two to digest three gigabytes, and six to start. A process its
  commands left running ends with the stop; its files and its images
  stay.
- A VM's snapshot is restored on the host whose store keeps it. A pool
  of hosts that restore one another's VM workspaces is the trigger for
  a shared store, such as a cloud's disk snapshots.
- A machine's image must be a Linux whose packages hold Docker.
- On Lima, a VM workspace with no egress is refused; a cloud's machines
  may close it.
- With a secret the catalog may inject, each snapshot's scan reads the
  disk's data. A run's end that waits more than a minute on the scan is
  the trigger to scan only what changed since the snapshot before.
