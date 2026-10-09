# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.14.0 (2026-10-09)

The engine's base moves to the guideline at v0.59.0, with v0.58.0 in
it. They bring a read cache keyed by the tenant's generation, a notice
the channel hands over once, a purge's tenant context and a member's
admission, and an investigator's skill for an integration gone silent.
A resource's owner updates it, a tenant reads its leases back as a
history, a kind may refuse an ask, and a line answers its places from
one replay. The engine's `core` migrations move above the guideline's
new one. Minor: nothing is reversed.

### Added

- From the guideline's v0.58.0, in the scaffold: `ReadCache`, a read
  cache keyed by the tenant's generation (ADR 0095); a notice the
  portal's channel hands over once, as its cursor passes it (ADR 0096);
  `TenancyManagerInterface.sweep_context`, the tenant context a purge
  across tenants acts in, and the org's gate that `add_member_to` asks
  before a new member is written (ADR 0097); and
  `ops-integration-silent`, an optional investigator's skill that says
  where an integration's inbound deliveries stop (#86).
- From the guideline's v0.59.0, in the scaffold: an owner's update of a
  resource, `update_statement` with a `ResourceUpdate`, after which its
  line is offered it again; a lease history, newest first and a
  tenant's own, at `GET /v1/leases` and in the Python client's
  `lease_history` (migration `core` `202610091856`); and
  `AskCheckInterface`, a kind's check that refuses an ask before
  anything of it lands. ADR 0098 records them (#87).

### Changed

- From the guideline's v0.58.0 and v0.59.0, in the scaffold: a write
  bumps the tenant's generation once its transaction commits, never
  before; a deleted tenant keeps its service context until a pass marks
  it purged; a grant lands only while the request still fits the
  resource as its row stands under the anchor's lock (`grant_fits`);
  and a line answers each place and estimate (`Place`) from one replay
  (#86, #87).
- The engine's eight `core` migrations move above the guideline's new
  `202610091856`, as `202610091857` to `202610091904` in order, their
  SQL unchanged. The first revises `202610091856`, so the role keeps
  one head, `202610091904`, and a layer on the engine re-points its
  first `core` migration to it (#87).
- The spec's links and the lenses' README cite the guideline at v0.59.0
  (#86, #87).

### Fixed

- From the guideline's v0.59.0, in the scaffold: the import-direction
  test lists `acme.infra.cache.read` with the infra interfaces, so a
  manager that takes a `ReadCache` through its constructor passes a
  copy's unit gate (#87).
