# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.29.0 (2026-11-24)

A model call's usage record holds its time to the first token; a kind
may prepare its workspace as its run's first model call goes out; and
the tree's dead concurrency column leaves its table. Minor, with no
reversal.

### Added

- A kind may declare `prepares_early`. A run of it that holds no
  instance starts its workspace's prepare as its first model call goes
  out, once the outage mark, the key, and the gate let the call
  through, so a run that parks before any call prepares nothing. The
  first call that reaches the workspace takes that prepare. A run that
  reaches none lets the instance go before its end, park, or yield step
  is written, takes no snapshot of it, and counts it in
  `acme_workspace_prepares_unused_total` by kind. The spec's Runtime
  paragraph and Workspace Snapshots' "When", lens TOL-14, and ADR 1048
  say it (#151).
- A model call's usage record holds `first_token_ms`, its time from the
  request to the first part the provider streams, beside `latency_ms`,
  null when no part came; `acme_model_first_token_seconds` observes it
  by provider. An activity migration adds the column, and the operator
  plane's usage read carries it. The spec's Usage and Cost section and
  ADR 1014 name it (#150).

### Removed

- `agent_trees.concurrency`: a core migration drops the column the code
  stopped reading in 0.28.0, as ADR 0038 times it; its down adds it
  back empty (#149).
