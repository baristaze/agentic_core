# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.25.0 (2026-11-21)

The first model call never waits for a workspace, a VM workspace keeps
its directory sealed in the buckets rather than its disk, and a kind's
loop may run in the request that asks it. Minor, with three reversals:
a workspace is prepared at the first tool call that reaches it, no
longer before the first model call, and the loop hooks no longer
receive one before that call; a VM workspace's snapshot is its
directory, no longer its disk (ADR 1029 now covers a base alone); and a
web service runs a request kind's loop in its request (ADR 1044 records
the deviation from the guideline's ASY-15 and NET-19).

### Added

- A kind declares where its loop runs: `runs_in` is `worker`, the
  default, or `request`. A request kind holds no workspace, no
  sub-agent, no job, and no tool behind an approval, within 10 steps
  and 30 seconds, and a root built with one that breaks a rule is
  refused, naming each breach. `POST /v1/agent-sessions/questions` and
  `POST /v1/agent-sessions/{id}/questions` run its loop in the request
  and stream server-sent events: the session, its text, a ping after 15
  seconds of quiet, and one end with the outcome, its cause, the steps,
  and the usage. A park ends the stream with its cause and answers each
  open call as not run; no work item is written, and no recovery or
  sweep touches the session. The scaffold offers an example request
  kind, `assistant`, where a product declares none (ADR 1044, #136).
- `LoopHooksInterface.workspace_prepared`: a layer above learns of a
  prepared workspace once, before the call that reached it runs, and
  appends its own steps there (#135).

### Changed

- Before a loop's first model call, a workspace provider only checks the
  isolation spec (`check`), and a spec it can never meet is refused
  there with its cause, nothing made. The workspace is prepared at the
  first tool call that reaches it (`ToolSpec.workspace`), and only that
  call waits: a prepare that must wait parks the loop with the call
  open, and it runs once when the workspace comes. A run that reaches
  no workspace prepares nothing and takes no snapshot. A refusal that
  cannot clear answers every open call before the loop ends. The rule
  of two reads the session's own egress, whatever a call reaches
  (`gate(..., egress=)`). The snapshot a run starts from counts a job's
  call in the run that started it, and no call no run started (ADR 1043,
  #135).
- A VM workspace kept by snapshots keeps its directory alone: measured
  against a bound before it is packed, packed in the guest under a time
  bound of its own, and kept in parts, each sealed under the session's
  key, beside a sealed manifest; the parts of a failed archive are
  removed. A restore starts a new VM on the base beside the one it
  replaces and lets the old one go only once every part is unpacked,
  each held to its keyed hash, so a bad part loses nothing. The model is
  told that what lay outside the directory comes from the base again. A
  provider implements `keeps_directory`, `pack`, `read`, `drop`, and
  `restore`, or takes `KeepsWholeMixin` (ADR 1045, #134).

### Fixed

- A test of a pack that fails no longer reads a loaded machine's slow
  measure as a timeout (#137).
