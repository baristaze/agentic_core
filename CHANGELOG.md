# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.13.0 (2026-10-09)

The engine's base moves to the guideline at v0.57.0, with v0.56.0 in
it, which bring a kind's own lane in the relay, a tenant's own cap on a
lane, and a lease kept by the worker of the job its grant starts. The
relay is the guideline's, and the loop's lane is one line in its
registry. A job tool asks in line for the resource its work runs on,
and the grant starts its job. Minor: nothing is reversed.

### Added

- From the guideline's v0.56.0, in the scaffold: a kind's own lane,
  named in `WORK_LANES` and read by `relayed_lane`, on which the relay
  lands each item of the kind; a tenant's own cap on a lane,
  `TenantCap`, which an operator sets at
  `/v1/admin/orgs/{org_id}/work/lanes/{lane}/cap` and the claim holds in
  place of the lane's (migration `queue` `202609280003`); ADRs 0092 and
  0093 (#83).
- From the guideline's v0.57.0, in the scaffold: a grant that starts a
  job, whose worker starts, renews, and ends the lease under a
  `JobClaim`; a job's window to start in; `WorkManagerInterface.holds`;
  a renewal that names its length; labels as free text; a term of up to
  seven days (migration `core` `202609280002`); ADR 0094 (#83).
- A job tool's run may ask in line for the resource its work runs on,
  and name the request in `JobStarted`. The loop parks on `resource` at
  once, with its place, until the job's deadline. The grant starts the
  job in its own commit and moves the park to the job's, with no notice
  and no model call between, and the job's result answers the call. A
  request that ends without a lease answers the call, and no job
  starts. A loop that stops leaves its lines before it cancels its
  jobs, and every cancel of a job that asked in line names its request.
  The spec's Parking and Long-Running Jobs and TOL-09 state it, and ADR
  1026 records it (#84).

### Changed

- The relay and its lane registry are the guideline's. The engine drops
  its own `WORK_LANES`, `relayed_lane`, and edit of `enqueue_relayed`,
  and registers `LOOP`'s lane, `loop`, as one line in the guideline's
  `WORK_LANES`. The session runner's lane reads `relayed_lane` (#83).
- The engine's first `core` migration, `202610020100`, revises the
  scaffold's new head, `202609280002`, so the role keeps one head
  (#83).
- The spec's links and the lenses' README cite the guideline at v0.57.0
  (#83).

### Removed

- `LOOP_LANE`: the loop's lane is `relayed_lane(WorkKind.LOOP)` (#83).
