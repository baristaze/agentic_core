# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.6.1 (2026-10-05)

A deleted session no longer loosens the policy of the sub-agents beneath
it, and a cancel reaches every sub-agent past a deleted one. Patch: two
fixes, and nothing is reversed.

### Fixed

- A session with a sub-agent at work below it (pending, running, or
  parked) is not deleted; the refusal names that sub-agent. A
  sub-agent's call is decided under the kind of every session above it,
  read from each stored row whether or not it is marked deleted, and a
  sub-agent whose ancestor is purged ends its run `errored` before any
  model call, since no layer can stand in for a kind that is gone.
  ADR 1019 says so (#55).
- A cancel's walk skips a deleted session and goes on, so every child
  after it, and every session beneath it, is cancelled; a moved
  deadline's unlock walk skips a deleted member the same way (#56).
