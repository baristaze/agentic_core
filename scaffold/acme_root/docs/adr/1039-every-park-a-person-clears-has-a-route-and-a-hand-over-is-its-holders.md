# ADR 1039: Every park a person clears has a route, and a hand-over is its holder's

**Status**: accepted (2026-10-12)

## Context

A loop parks for a person on a question, an approval, a passed
deadline, the step guard, a principal who no longer holds, a spender, a
lost workspace, and a hand-over, and it waits on a job's completion. The
managers that clear the deadline, the principal, the hand-over, and the
job had no route: only a generic `unlock` control or a cancel reached
such a session. Assigning a principal did not wake the loop parked on
the old one.

The `unlock` control clears any park, a hand-over included, for anyone
who may write, a program on an API key too. The hand-over did not say
who held the environment, so nothing could tell the holder from anyone
else.

A provider park on a key or an account error had no retry time. The fix
is made at the provider, or where the platform keeps its key, and
nothing tells the engine, so the session waited for an unlock no one
knew to send.

## Decision

**A route for each park.** The session API moves the deadline of the
session's tree (`PUT /deadline`), assigns the caller as its principal
(`PUT /principal`), takes its environment over and gives it back
(`POST /take-over`, `POST /give-back`), and completes a job it waits on
(`POST /jobs/{key}/completion`). Each calls the manager that clears the
park, held to the permission that manager checks, and answers the
session as it stands. A question keeps its message, an approval its
decision, and the step guard, a spender, and a lost workspace the
`unlock` control: a person looks, and lets the loop go on.

**An assignment wakes its park.** `assign_principal` unlocks a loop
parked on a principal who no longer holds, as `set_deadline` unlocks a
loop parked on the deadline. The route takes no body: the caller
becomes the principal. A hand-off that names another person can add one.
A sub-agent runs under the principal it took at its spawn and is never
assigned itself, so an assignment on the root reaches each sub-agent that
holds the principal it replaces: they take the root's new one, never
more, and their parked loops wake too.

**A job's cost is not taken over the wire.** A job that spends settles
at the cost its runner reports, or at its whole hold. A caller who may
write could report a smaller cost, so the route takes none, and the
hold counts whole.

**A hand-over names its holder.** `take_over` records the caller on the
park. Only that person gives it back or unlocks it, and only in person,
on a session of their own, never on an API key. Taking over a session
another person holds is a conflict. A cancel still ends the loop.

**Held in history order.** A control names the principal who sent it.
The session's projection clears a held hand-over only on its holder's
own unlock, so a control that lands after a take-over is read against
it, whatever its sender saw. The inbox refuses another sender's unlock
up front, so they learn it. The engine's own unlock is no principal's,
and clears no held hand-over.

**A key or an account park tries again.** A `billing` or `credential`
park tries again after a wait, five minutes by default, and its cause
says who must act: an operator for the platform's key, an admin for the
org's. A missing key keeps no clock: saving one is the write that wakes
it.

## Consequences

- No park a person clears waits for a cancel alone.
- A product that saves a tenant's key, adds a price, or raises a budget
  wakes the sessions parked on it with `wake_parked`.
- A refused key or a short account costs one failed call per parked
  session per wait. The provider bills no tokens for a refused call.
- A step's header and a session's park are stored as JSON, and both
  read an absent holder or sender as none: a hand-over park that names
  no holder is cleared by any unlock.
