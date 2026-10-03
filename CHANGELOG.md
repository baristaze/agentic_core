# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.3.0 (2026-10-03)

A tool's long job, and reads past a long line. Minor: rules are added,
and no released rule is reversed.

### Added

- A `job`-mode tool starts its work and answers a handle; the loop parks
  on `job`, holding no runtime, and the job's completion, checked against
  the session's history, writes the call's response before any further
  model call. The job's deadline is never past the tree's; at it the job
  is cancelled and answered as a timeout, and any end of the loop cancels
  it too. A spending job passes the budget gate before it starts, at its
  tool's hourly rate; its hold is released only when the tool refused
  before starting, and a completion's cost is bounded. ADR 1013, and the
  scaffold-tool skill scaffolds a job tool (#28).
- `read_attachment` takes an offset into its range's one line or page and
  answers the offset the next read starts at, so a one-line export or a
  large page is read whole (#27).

### Changed

- A layer's copy runs its stack under a compose name no stack or volume
  uses, and takes that stack down once its gates have run (#25).
- The spec's running example and the scaffold's tests tell their story in
  software nouns: a checkout service that drops an order (#26).
