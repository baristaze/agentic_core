# ADR 1017: A wall is checked at each call, and a run reads what is new

**Status**: accepted (2026-10-04)

## Context

The engine's walls were each checked at one point, and each point missed
a case. The API classed only the engine's own tools, so a member could
message an admin's session whose kind offers a product's configuration
call. A call's class was asked of its principal at the start, never
again. The outward ceiling read only what a call's target said of
itself. A child took its parent's mark but not its private data. A
decision sent on an API key counted as a person's. Open-egress container
workspaces shared Docker's default bridge, where each reaches every
other.

Two costs grew without bound. The sweep never marked a deleted tenant
purged while its ledger stayed, so every pass ran each of its purges
again. A loop read its whole history again for every turn and every
render.

## Decision

**Every product tool is classed.** The API takes the product's tool
catalog. A registry name no catalog classes is refused, never skipped.

**A call is asked of its principal as it is now.** The gate refuses a
call whose class's permission (ADR 1012) the principal's live context
lacks, as ADR 1007 asks.

**The ceiling reads the call's reach.** A call `reaches_outward` counts
as outward is outward at the platform's ceiling, whatever its target
says.

**Private data passes like the mark.** A session stores whether it holds
private data. Its maker sets it from its kind and its tools' secrets,
and a session spawned or handed over takes its source's. A session
stored before this is taken to hold it.

**An API key decides as a program.** A decision sent on one is recorded
with that credential's actor, and approves nothing a person must.

**Open egress joins one bridge where no container reaches another.** It
is `acme-ws-open`, made with traffic between its containers off, and one
that stands with it on is refused. It is one bridge, not one a
workspace: Docker's default address pools hold about thirty. The host's
metadata service is the host's to close. A host that runs these
workspaces requires metadata tokens with a response hop limit of 1, so a
container one hop behind the bridge gets none.

**A deleted tenant is marked purged once its other rows are gone.** No
serving login deletes a hold or a settlement (ADR 1006), so the ledger
stays, and it no longer holds the mark.

**A run reads its history whole once.** Each turn after reads only the
steps added since, and the render takes the run's history.

## Consequences

- A member who holds `write` alone steers no session into a call of a
  class it lacks, by a start, a message, or a demotion between calls.
- A child of a session that holds private data is held by the rule of
  two as its parent is.
- A deployment that runs the container provider on a cloud host sets the
  hop limit there; the provider cannot set it from Docker.
- The ledger's retention, when decided, runs across tenants.
- A turn of a long session reads what was added since the last one.
