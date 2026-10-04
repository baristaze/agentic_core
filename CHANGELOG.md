# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.5.0 (2026-10-04)

Every billed model call leaves a content-free usage record, a child's
report wakes its parent, each wall is checked at the call, and the
engine builds on the guideline at v0.51.0. Minor: new routes, a lens
group, and skills come in, and no released rule is reversed. A spawn
refuses a kind that names no share, so a product's spawned kinds name
one on upgrade.

### Added

- Every billed model call writes a usage record with no content, in
  every storage mode: session, tree, loop, response step, agent kind and
  version, role, provider, model, the token classes, cost, and latency.
  A call settled at its whole hold is recorded at the hold and marked
  `settled_whole`, with the partial reply's tokens where there are any.
  The records are append-only in `activity.usage_records` beside the
  ledger; both purges keep them, and they never keep a deleted tenant
  from being marked purged. A failed write is logged and never fails its
  call. `GET /v1/admin/orgs/{org_id}/sessions/{session_id}/usage` gives
  a read operator a session's records with per-loop and per-session
  rollups, and `StepView` carries a step's usage. ADR 1014 and a
  migration in `activity` (#41).
- The Economy lens group: the spec's Economy section, ECO-01 to ECO-08 in
  `lenses/economy.md`, and the `agentic-review-economy` skill.
  `agentic-review-full` and the reviewer agent run ten groups, and a
  range or a commit followed by a path reads only the files it changes
  under that path. `agentic-check` knows the ECO group (#39).
- The spend skills `ops-loop-spend` and `ops-session-spend` reconcile a
  bill from the usage records: each turn's tokens and cost, context
  growth, cache-miss streaks, compaction churn, calls settled whole, and
  cost by kind, version, role, and model. A read that is cut or live is
  reported as such and never reconciled. `audit-model-spend` moves into
  the scaffold, worded with the engine's nouns. A test holds that every
  spend skill reads only the usage route and its own identity, with the
  read token (#42).
- A child whose loop ends writes its report into its parent's inbox as a
  waking input naming the child, taken from its accepted result or its
  last answer; above ECO-03's bound the report is an artifact with a
  preview and a handle. A park on a person and a cancel each note the
  parent. The report carries the child's untrusted mark and its private
  data, so the rule of two holds across the tree, and it is no
  principal's message: it answers no parked question and brings back no
  archived parent. ADR 1018 (#47).
- `.github/workflows/release.yml` fast-forwards `release` from `main`,
  then tags and publishes the GitHub release behind
  `.github/workflows/human-approval.yml`, the reusable step that refuses
  without a required-reviewers rule. A private repository can have that
  rule only under GitHub Enterprise, so on Free, Pro, or Team every run
  refuses; `CONTRIBUTING.md` says so, and until the repository has the
  rule a release is tagged and published by hand (#40).
- Benchmark run 1 of the spec's browser-judge scenario is recorded, with
  `benchmark/browser/prompt.md` for run 2 onward. A run folder is a
  record: the checkers and markdownlint leave it out (#37).

### Fixed

- The API checks a product tool's class on a start and a message, and
  refuses a tool name the catalog cannot class (#43).
- A tool call is checked against its class's permission in the live
  context every time. The platform's outward ceiling holds a call whose
  target says it is outward, or, when the target is silent, whose class
  is outward. A command under open egress stays a leg of the rule of two
  and runs unattended in an unmarked session. `agentic-scaffold-tool`
  says what a tool's target answers for the ceiling (#43).
- A session spawned or handed over by one that holds private data holds
  it too: `agent_sessions.holds_private`, true by default and kept
  through a roll and a rollback. It comes with a migration (#43).
- A decision sent on an API key is recorded as a program's and approves
  nothing (#43).
- Open-egress container workspaces join a bridge where none reaches
  another, and a bridge of that name with inter-container traffic on is
  refused (#43).
- A deleted tenant is marked purged once its rows are gone, its ledger
  kept, so a sweep no longer runs the tenant's purges again on every
  pass. ADR 1017 records each wall, the host's metadata hop limit, and
  the reads that follow under Changed (#43).

### Changed

- A spawn's and a hand-off's objective holds at most 8,000 characters; a
  longer one is refused with the bound named (#45).
- An agent kind a spawn can start names a share. The spawn writes it as a
  budget on the child's own session before the child wakes, so the child
  stops at its share while its tree still has room, and a share never
  raises the tree's budget. A spawn refuses a kind that names none,
  before it takes a slot, so a product's spawned kinds name a share on
  upgrade. `agentic-scaffold-agent-kind` says which kinds name one, its
  form, its floor, its unattended default, and what the parent kind
  holds (#45).
- A step append checks for a stored step by its session and id alone. A
  loop reads its history whole once a run, and each turn only what was
  added (#43).
- The engine builds on the guideline at v0.51.0: a production run asks a
  person once, on the job that holds the deploy credential;
  `human-approval.yml` is the reusable step, and `grant-operator.yml` and
  `state-unlock.yml` check its rule before their credential on
  production; `scripts/branch_rulesets.sh` sets the rulesets on `main`,
  `release`, and `scaffold`; the scaffold's 47 guideline ADRs are
  compacted; and the ops audit test's database takes a name of the run's
  own. The spec and the lenses cite v0.51.0 (#44).
- The engine's ADRs 1005, 1007, 1011, 1012, 1014, and 1017 state what
  holds, in the present tense. ADR 1017's context names the points a
  wall is checked at, and each near miss stands in its decision. No
  decision changes (#46).
- The spec's title is An Engine for Long-Running Agents; the subtitle
  carries the code name and the qualifiers (#38).
- The README links each part it names, and says that while the
  repository is private only an account that can clone it can add the
  marketplace (#36).
