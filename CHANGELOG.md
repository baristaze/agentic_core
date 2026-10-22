# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.22.0 (2026-10-22)

The engine's base moves to the guideline's v0.64.0: a copy's gates take
less time, its suites on every core and side by side. Minor.

### Changed

- The base moves to the guideline's v0.64.0. A copy's `make test-unit`
  runs on one worker per core and ends on its ten slowest tests (ADR
  0105: a unit test holds nothing another can reach, its id fixed at
  collection); its CI starts the integration, telemetry, and image jobs
  beside the fast gate; the integration wipe empties only the tables
  written since the last one; `make openapi` cuts the schema in linear
  time; and arch-check is pinned at v0.64.0 (#125).
- This repository's CI runs each copy's `make check`, its integration
  suite, and the release-before step as jobs of their own, side by side
  (#124).

### Fixed

- The mint test counts the process as booted, so its logging never binds
  a captured stream that later tests on its worker write to (#125).
