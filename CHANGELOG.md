# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.28.0 (2026-11-23)

A response's tool calls run in order, and the texts say so; a tree
bounds no concurrency; a person's view of a session names the engine's
notices and shows a failed call without the advice meant for the model.
Minor, with three reversals named below.

### Changed

- The spec's loop sketch and Execution section, TOL-08, ADR 1023, and
  the agents README say what the loop does: a response's calls run one
  after another, in the order the model made them, every request
  persisted before any call is decided. A call that waits for a person
  is put off: the calls after it run in order, and it is answered once
  the person decides. ADR 1047 holds the decision and its measure.
  Reversal: the texts no longer say calls run concurrently when their
  tools allow it (#146).
- The step view gives a failed call's text as the tool's own text, its
  class in `failure`, without the advice the model reads; the stored
  step and the model's request keep the advice, byte for byte. The
  CLI's step line prints a message or environment change the engine
  wrote as `from the engine: ...`. The spec's History section and lens
  STP-13 say what a person's view shows, and the client types export
  `Actor`. Reversal: a client that read the advice from a failed call's
  `text` in the step view no longer finds it there (#147).
- `agentic-upgrade-scaffold` reads a role's scaffold head by its
  chain's parents, the revision no other names as its `down_revision`,
  never by the file name (#145).

### Removed

- A tree's concurrency bound: the spec's row, AGT-05's clause,
  `TreeLimits.concurrency`, and `AgentTree.concurrency`; a kind whose
  tree names it is refused. `agent_trees.concurrency` leaves the
  mapping and stays in the table until a later release drops it (ADR
  0038). Reversal: a kind that set the bound no longer builds (#146).
