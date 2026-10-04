# ADR 1007: A delegated call asks the person's permissions again

**Status**: accepted (2026-10-02)

## Context

The guideline's work queue authorizes work once, at enqueue: the claim
rebuilds the enqueuer's principal under the service role, and a member
who has left does not stop the work they asked for. A handler that asks
the live membership again instead departs from that, and the guideline
asks for the decision to be recorded.

An agent's loop is such a handler. A session can run for months, and an
assistant acts for whoever asks it. Its tool calls are the agent's acts
in the world: read a record, post a comment, push a branch. Running them
on a permission checked once, when the session was made or when its
loop was enqueued, lets a person whose access was taken away keep
acting through the agent.

## Decision

**Every tool call asks the adopter's transition for its principal's live
context.** The attribution manager asks before each call, never once per
loop, and the call runs under the context the transition answers with:
the permissions the principal holds now, never the system's.

**The mode picks the principal.** A delegated session's call runs under
the person who asked last in what the model read: the speaker the request
that led to the call recorded, never someone whose message landed after
it. A delegated child's call runs under the principal it inherited,
whoever speaks to it, so no child holds more than its parent. A message
is said in the name of the context that appends it, so no one asks in
another's name. A revoked person's next call is refused
(`AuthorityRevoked`) and denied. A steady session's call runs under its
one fixed principal, and when that principal lapses, its calls
stop (`PrincipalLapsed`) until a person takes the session over.

**The transition is the adopter's.** It is one operation of the
adopter's tenancy manager, handed to the root. A root handed none wires
the tenancy manager's own, `member_context`: a person's role as their
membership holds it at the call. It answers for no service principal,
since the tenancy manager grants none, and for nobody who has left.

**A key's cap holds at the call.** A message said on an API key records
the key with its principal, whatever its caller wrote. A call made on it
runs with the key as its credential and its role capped at the key's, and
a key revoked or expired answers for nobody.

**The decision is on the record.** A tool request names the principal it
ran under and the mode that chose it.

## Consequences

- A permission taken away stops the agent's next tool call, in every
  session at once, with no session to find and stop.
- Each tool call costs one read of the membership. A loop's tool calls
  are few beside its model calls, and nothing caches the answer.
- The loop's own plumbing (claiming, appending, settling) still runs
  under the context the claim builds; only tool calls ask again.
