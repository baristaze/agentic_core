# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.12.0 (2026-10-09)

The engine's base moves to the guideline at v0.54.0 and then v0.55.0,
which bring leases on a resource and a shared outage signal. A session
waits in line for a leased resource, parked, and parks on the
guideline's outage signal, which takes the place of the engine's own.
The engine's approval rules state their classes and targets in its own
words. Minor: nothing is reversed.

### Added

- From the guideline's v0.54.0, in the scaffold: leases on a resource,
  with the `leases` namespace, its `core` tables and hooks,
  `/v1/leases`, the lease sweep, the Python client's `LeaseClock` and
  `Fence`, and ADR 0086 (#78).
- From the guideline's v0.55.0, in the scaffold: a tenant's cap on a
  shared lane, held at the claim; the outage signal,
  `OutageSignalInterface` on the infra root as `get_outages()`; the
  maintenance loop's chores; the tenancy manager's `member_context`;
  the portal's hint reader and `GET /v1/users/{user_id}`; ADRs 0087 to
  0090 (#80).
- A session is a waiter of the guideline's leases. A tool that answers
  `InLine` asks under its call's key and answers at once with its
  place. When the turn ends while an ask waits, the loop parks on
  `resource`, naming the request, its place, and its estimate, also in
  the API's park view, and calls no model until a grant or an end. A
  `LEASE_NOTICE` item unlocks it, and the model hears each answer once,
  before its next call. A loop that ends, or parks on the tree's
  deadline, leaves every line, and a deadline park also releases its
  leases. The spec's Parking cites Leases on a Resource, BND-10 holds
  it, and ADR 1024 records it (#79).

### Changed

- The loop reads, marks, and clears the guideline's outage signal,
  keyed by the call's key by name: a tenant's own under its org, the
  platform's under the system scope. A session that reads a mark parks
  at once, and a call that answers clears it. The spec's Provider
  Errors and MOD-07 cite the guideline's CON-24 and state only the
  session's park, and ADR 1025 records it (#81).
- A session's loop runs on its own lane, `loop`: the relay lands `LOOP`
  items there, and the runner claims there and refuses to start on any
  other. ADR 1011 says so (#81).
- The engine's tenancy `member_context` is now `delegated_context`, and
  the scaffold's `member_context` keeps the name (#80).
- The engine's first `core` migration, `202610020100`, revises the
  scaffold's new head, `202609280001`, so the role keeps one head
  (#78).
- Policy and TOL-06: what waits for a person is what is destructive,
  outward-facing, or expensive, or in a class the ceilings mark as never
  unattended. Approvals and TOL-07: a class grant is never for a
  destructive class, or for one the ceilings mark as never granted
  whole, and an approval bound to a target chosen later names any one of
  a set of equal resources, for the same tool and input, the target
  aside. A domain class in the tool classes' table is one an adopter
  adds (#77).
- The spec's links and the lenses' README cite the guideline at v0.55.0
  (#78, #80).

### Removed

- The engine's own outage signal: the conformance kit and the infra
  capabilities drop it, its mark's error kind goes, and a platform
  supplies none, since the guideline's cache impl is the fleet's. The
  guard it held, a session parked at once on a known outage, holds
  under the guideline's signal (#81).
