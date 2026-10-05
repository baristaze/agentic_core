# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.5.1 (2026-10-05)

The engine builds on the guideline at v0.51.1, so a copy stops and
starts its local stack without removing it. Patch: a base move, and
nothing is reversed.

### Changed

- The base is the guideline's scaffold at v0.51.1: `make stop` stops
  every container of the copy's stack and removes none, and
  `make start` starts what exists, waiting on health, with no build,
  migration, or seed. The spec's and the lenses' citations of the
  guideline read v0.51.1.
