# ADR 1028: A workspace base is built once for its tenant and kept as a snapshot

**Status**: accepted (2026-10-10)

## Context

Every workspace starts on one image the process names, with nothing set
up. An agent whose workspace has no network cannot install what its
work needs. One with open egress can, but then every command after its
first tool result reaches the world on content the model read. A
platform wants both: the packages installed once, from a registry, and a
workspace that reaches nothing.

A snapshot ([ADR 1027](1027-a-workspace-is-snapshotted-whole-and-a-restore-a-rewind-or-a-fork-starts-from-it.md))
already keeps a workspace whole, and a prepare starts one from it. A
base is that same capability, taken once for many workspaces.

## Decision

**What a base is.** An isolation spec may name a base: an image, an
ordered list of setup commands, and the egress the setup may use. A base
with no setup is the image alone, and a spec with no base runs on the
provider's own image.

**How it is built.** The first prepare of a workspace on a base with
setup builds it. The setup runs in a workspace of its own, on the base's
image, with the setup's egress and the spec's limits, and the provider
snapshots it. Each command runs through the transport as `sh -c`, in the
workspace's directory, with no secret and no variable of its own, so its
environment is the image's alone. No model runs, and nothing of a
session is in it. What a command printed is not kept. The build's
workspace and the records of its commands go, however the build ends.

**What a setup may do.** A setup installs what its workspaces need, a
system's packages among them, and a package manager changes a file's
owner and drops to a user of its own. So the setup's workspace holds
those powers: a container holds the runtime's default capabilities
less raw sockets, takes no new privileges, and is never privileged.
That is safe because a setup runs the commands a person declared, no
model's, with no secret and nothing of a session. A workspace a
session uses, on a base or not, drops every capability. A container is
reused only under the spec and the role it was started to, so a setup's
container never serves a session.
The snapshot is scanned for the values of the catalog's secrets, as a
session's is; one that holds a value is refused.

**Where it is kept.** In the `snapshots` bucket, under the tenant's own
prefix, at `workspace-bases/<hash>`: a sha256 of the mode its workspaces
run in and of the base whole, its image, its commands, and its egress. A
changed base is another key, so it is built again and never served
stale. Another tenant's prepare reads under its own prefix and builds
its own. A base is not sealed under a session's key: it holds no
session's content, so nothing a session's key erases is in it, and
sealing it under one session would bind every other session's workspace
to that session's key. It is the tenant's, like its policy, and goes
with the tenant's purge. The store's encryption at rest is the bucket's.

**One build.** A prepare that finds no base claims its build on the
cache every worker shares, for as long as a build may take (30 minutes
by default). It reads the store again, builds, keeps the base, and lets
the claim go, whether the build ends well or not. A prepare that finds
the claim held is refused with a refusal that clears: its loop parks on
the resource and asks again, and then reads the one build. The cache
fails open, so with no cache two prepares may each build, and the later
write keeps an equal base. A claim lost with its process ends with its
window.

**A failed setup.** A command that exits non-zero, or runs out of time,
stops the build: nothing is kept, and the prepare is refused with the
command's place, its text, and its exit code. The refusal does not clear,
so the loop ends `errored` and says why. The next prepare builds again.

**Starting a workspace on it.** Each instance the provider starts starts
from the base's snapshot. A container starts on the image the snapshot
names, by its id, with the setup's writable layer copied in and the
paths it removed removed again. The setup's files are copied into the
workspace's volume only when the volume is new, so a workspace found
again keeps its own files and gets the setup's layer back. A restore
from a session's snapshot wins over the base, since that snapshot holds
it already. A workspace runs with its own spec's egress, never the
setup's. The container provider gives a setup no egress or open egress,
and refuses an allowlist, as it does a workspace's. A provider that
cannot snapshot refuses a base before anything is made, and none starts
a workspace on a base with setup without its snapshot.

**An image the host lacks.** A base's snapshot names its image as any
snapshot does: by its id, and by the digest a registry serves it under,
which a host that lacks it pulls. When the image cannot be had, nothing
of the session is lost with the base: it is dropped, the prepare is
refused with a refusal that clears, and the next one builds it again.

**The seam.** Building needs only what every provider already offers:
`prepare`, told when it prepares a setup's workspace, `snapshot`,
`purge`, and the transport's `run`. A VM provider serves a base by
those same calls, its disk for the container's layer.

## Consequences

- A base's image is named as the spec gives it, and its snapshot pins
  the image it was built on. A tag that moves does not rebuild a base: a
  platform that wants the newer image names a new base, or rebuilds.
- A base no spec names any more stays in the store until the tenant's
  purge. A tenant's store of bases outgrowing what its live specs name
  is the trigger for removing the ones none names.
- Each prepare on a base reads its whole snapshot from the store. A
  base whose read shows in a loop's start is the trigger for keeping it
  on the host that starts it.
- A setup's egress is open or none. A setup that must reach one
  registry and nothing else waits on an egress proxy.
- A build that dies with its process leaves its container behind; its
  claim ends with its window, and the next prepare builds again.
