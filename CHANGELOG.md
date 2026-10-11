# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.15.0 (2026-10-11)

A workspace stays a cache, and may now be kept whole: a run snapshots a
workspace whose durability is `snapshot` at its end, the next run
starts from it, a principal rewinds to an earlier one, and a sub-agent
forks from its parent's workspace as it stands at the spawn. A workspace
may start from a base, an image and its setup built once per tenant. A
VM per session runs on a machines interface, local on Lima, with Docker
inside. An agent reads back a kept or elided result with
`read_artifact`. Minor, with one reversal named: `Spawn.fork` is
removed.

### Added

- `read_artifact`, a native tool that pages through a result kept as an
  artifact and reads back a result compaction elided, by the handle the
  notice or the stub names; it reads only the calling session's
  artifacts and steps (lens WIN-08) (#89).
- Workspace Snapshots, an `optional` part of the engine: a workspace's
  durability (`cache` or `snapshot`), a snapshot taken by the run that
  holds the workspace at its end and named by a `snapshotted` step, a
  restore of the latest one at the next run, a `restore` control that
  rewinds, a snapshot sealed under the session's key and stored through
  the buckets interface, refused when it holds an injected secret's
  value, and removed by a purge. A container's snapshot holds its whole
  filesystem and names its image by a pullable digest (ADR 1027, lens
  TOL-15) (#90).
- Workspace bases: a spec may name an image, setup commands, and the
  setup's egress; the base is built once per tenant, kept as a
  snapshot, and every workspace on it starts from it with its own
  egress. A setup's container keeps the runtime's default capabilities
  less raw sockets; a session's workspace drops every one (ADR 1028,
  lens TOL-16) (#91).
- A fork snapshots the parent's live workspace at the spawn, through the
  spawning call's run, before a slot is taken or a child is made; a
  parent whose workspace is a cache forks too (ADR 1030) (#92).
- A VM workspace: `IsolationMode.VM` gets a provider over a new
  machines interface (`acme.infra.machines`: an interface, a Lima
  implementation, a twin, and a null), one machine per session with
  Docker inside. A VM's snapshot is its disk, cloned copy-on-write and
  held to a sha256; a key's revocation destroys the disks kept for the
  session's snapshots. The Lima probe refuses before any machine starts
  when there is no hypervisor or its socket path would not fit. Every
  provider gains `held`, `keep`, and `discard` (ADR 1029) (#93).

### Changed

- A workspace provider's `prepare` takes `snapshot=`, `base=`, and the
  keyword `building=`, and a provider snapshots, refuses a snapshot it
  cannot take, and keeps or discards a disk; an adopter's own provider
  and broker implement `snapshot` and `detach_all` (#90, #91, #93).
- The large-result notice and the elided stub name `read_artifact`;
  `engine_tools` takes the windows manager (#89).
- The spawn tool's timeout is ten minutes, since a fork now takes a
  snapshot (#92).

### Removed

- `Spawn.fork` and the tools manager's `latest_snapshot`: the agents
  manager's spawn takes the runtime's snapshot instead. A reversal
  (#92).
