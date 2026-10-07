# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.7.0 (2026-10-07)

The engine's base is the guideline at v0.52.0: a copy's local stack
runs each database role on its own Postgres, and a required job runs
the release before on a branch's schema. Minor: the base move adds a CI
gate and a migrate command, and nothing is reversed.

### Changed

- The base moves to the guideline at v0.51.2 and then v0.52.0. A copy's
  local stack runs `postgres-core`, `postgres-activity`,
  `postgres-queue`, and `postgres-admin`, each with its own port and
  volume, and `migrate ensure-logins` runs once per database; the cloud
  keeps one instance. The engine's purge login is made and granted on
  every database, the worker's purge URL is on the core instance, and
  the engine's own ports in `.env.example` are 55442 to 55445. A copy
  renames `<NAME>_POSTGRES_PORT` to `<NAME>_POSTGRES_CORE_PORT`, and a
  second checkout repoints every database URL.

### Added

- `release-before`, from the guideline: a job in a copy's CI that runs
  the integration suite of each release before on a branch's migrated
  schema, with `make release-before`, `migrate stamp`, and
  `scripts/release_before_deselect.txt`. The engine's CI runs it on a
  copy's branch that adds a migration, and it leaves the live tests out.
