# ADR 1017: A wall is checked at each call, and a run reads what is new

**Status**: accepted (2026-10-04)

## Context

A wall holds only where it is checked, and a call passes several
points: the API's classing of a session's registry, the gate before each
call, the outward ceiling, what a child takes from its parent, a
decision's actor, and the network between workspaces. A check at one
point misses what another lets through.

Two costs must stay bounded: the sweep's passes over a deleted tenant,
and a loop's reads of its own history.

## Decision

**Every product tool is classed.** The API takes the product's tool
catalog. A registry name no catalog classes is refused, never skipped:
a skipped one would let a member message an admin's session whose kind
offers a product's configuration call.

**A call is asked of its principal as it is now.** At each call, never
once at the start, the gate refuses a call whose class's permission
(ADR 1012) the principal's live context lacks, as ADR 1007 asks.

**The ceiling reads a silent target's class.** A target's explicit
`outward` wins, either way. A target that says nothing is outward at the
platform's ceiling when its class is not inward (read, write, execute,
spawn): a network, integration, configuration, credentials, or domain
call. A command in an open-egress workspace is not outward there: it is
a leg of the rule of two, and a session that lacks another leg runs it
under its class's policy.

**Private data passes like the mark.** A session stores whether it holds
private data. Its maker sets it from its kind and its tools' secrets,
and a session spawned or handed over takes its source's. Its column
defaults to holding it: every session stored before the column holds
it, and so does one an older release writes during a roll or after a
rollback.

**An API key decides as a program.** A decision sent on one is recorded
with that credential's actor, and approves nothing a person must.

**Open egress joins one bridge where no container reaches another.** It
is `acme-ws-open`, never Docker's default bridge, where each reaches
every other. It is made with traffic between its containers off, and one
that stands with it on is refused. It is one bridge, not one a
workspace: Docker's default address pools hold about thirty. The host's
metadata service is the host's to close. A host that runs these
workspaces requires metadata tokens with a response hop limit of 1, so a
container one hop behind the bridge gets none.

**A deleted tenant is marked purged once its other rows are gone.** No
serving login deletes a hold or a settlement (ADR 1006), so the ledger
stays, and marking the tenant purged never waits on it: a sweep that
waited would run each of the tenant's purges again on every pass.

**A run reads its history whole once.** Each turn after reads only the
steps added since, and the render takes the run's history, never the
whole history read again for each turn and each render.

## Consequences

- A member who holds `write` alone steers no session into a call of a
  class it lacks, by a start, a message, or a demotion between calls.
- The platform's outward ceiling holds every call of an outward class
  whose target is silent, and every call whose target says it acts
  outward. A tool whose call of such a class stays in the session's own
  work, such as a push to the session's own branch or a pull request on
  the bound repository, answers `outward: False`.
- Most development work runs unattended: a command in an
  open-egress workspace waits for a person only when the rule of two
  holds it.
- A child of a session that holds private data is held by the rule of
  two as its parent is.
- A deployment that runs the container provider on a cloud host sets the
  hop limit there; the provider cannot set it from Docker.
- The ledger's retention, when decided, runs across tenants.
- A turn of a long session reads what was added since the last one.
