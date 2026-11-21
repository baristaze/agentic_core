# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.26.1 (2026-11-21)

No worker runs a kind's loop that runs in the request that asks it.
Patch.

### Fixed

- The session runner completes a loop item for a session whose kind
  runs its loop in the request, without running it, so a loop its
  request bounds is never taken up by a worker, as ADR 1044 says. The
  runner's container keeps the kind catalog its managers are built from
  (#141).
