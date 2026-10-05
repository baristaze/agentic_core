# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.6.0 (2026-10-05)

A kind starts sub-agents through a tool and waits on them, three levels
deep and ten sub-agents by default, and the migrations' role check reads
the tables a chain dropped off the chain itself. Minor: rules come in
for starting and waiting on sub-agents, and none is reversed. A copy
that kept entries in `DROPPED_TABLE_ROLES` drops them.

### Added

- `spawn_sub_agent`, a native tool in the `spawn` class, starts a child
  under the call's own id, so a call asked again finds the child it
  made. Its kind defaults to the caller's; a bound it reaches (the
  count, the height, the deadline, the budget, a kind with no share) is
  the call's failure, which the model reads. A child's call is decided
  under its own kind's policy and each kind above it, and the strictest
  holds, so a model cannot choose its way past an approval (#53).
- `wait_for_sub_agents` parks the loop on `children` until a child's
  report wakes it, and is refused with no child running. A woken wait
  counts as no repeat toward the error streak, and once the tree's
  deadline passes the parent parks on the deadline, where its person is
  asked. ADR 1019 (#53).

### Changed

- `TreeLimits` defaults to height 3 (a root, its sub-agents, and theirs)
  and a count of ten sub-agents besides the root, and `AgentKind.tree`
  defaults to it. The spec's Sub-Agents section and park table, lens
  AGT-04, and the READMEs say so (#53).
- `ToolsManager.gate` takes `above`, the policy layers above a session,
  and `report_to_parent` unlocks a parent parked on its children (#53).
- The role check reads the tables a role's own up files drop, which the
  live role map does not hold, off the chain itself, and accepts them in
  that role's schema alone (`dropped_tables` in `migrate.py`) (#52).

### Removed

- `DROPPED_TABLE_ROLES` in `roles.py`, with the fold step that tidied
  it: the chain already says which tables it dropped (#52).
