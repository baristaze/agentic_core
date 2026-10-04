# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.4.0 (2026-10-04)

A deleted tenant's purge takes its sessions' workspaces, a recovered job
call runs once, and the engine builds on the guideline at v0.50.0.
Minor: the guideline's new rules come in, with one reversal.

### Fixed

- A deleted tenant's purge takes each session's workspace files and
  transport records through the root's purge hook, deletes only the rows
  whose holdings went, and marks the tenant purged only after. A call
  reads at most 100 sessions; one whose workspace cannot go keeps its
  row, and the call raises, so the next pass retries it (#33).
- A run that recovers a lost job call parks on the job it started: the
  job starts once, under one hold, and the budget gate is not asked
  again (#30).
- The requeue's plan test seeds settled items and live leases, analyzed,
  before it reads its plan, so the planner's choice no longer varies
  (#32).

### Changed

- The engine builds on the guideline at v0.50.0, through v0.49.0: the
  scaffold's nuke removes what Terraform does not own, production's
  create protects `release`, an outbound breadcrumb keeps no URL path,
  the portal's error reports keep no query, and the tracker's org and
  project come from `environments.json`. The spec and the lenses cite
  v0.50.0 (#34).
- Reversed: the guideline's NET-26 no longer asks a statement deadline
  of a migration's connection; the migration runner already sets
  `lock_timeout` and no `statement_timeout` (#34).
- The repository's `CLAUDE.md` lives under `.claude/`, so the plugin
  validates with `--strict`, and CI pins the Claude Code that checks it
  (#31).
