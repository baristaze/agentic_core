# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.7.1 (2026-10-07)

The engine's base is the guideline at v0.52.1: a copy's local Postgres
reads healthy only once it takes a connection over TCP. Patch: a fix
the base move brings, and nothing is reversed.

### Fixed

- A copy's local Postgres healthcheck asks over TCP
  (`pg_isready -h 127.0.0.1`), so a service that waits on it, such as
  `glitchtip-db`, is no longer refused while the image's init server
  listens on the socket alone. `infra/tests/test_local_postgres.py`
  holds every Postgres in the local compose files to it.
- A copy that names a test in `scripts/release_before_deselect.txt`
  keeps its unit gate: the release-before test builds its deselect file
  from the real file's comments.

### Changed

- The guideline is cited at v0.52.1 in the spec and the lenses.
