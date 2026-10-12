# ADR 1036: A budget closes once, and a closed one binds nothing

**Status**: accepted (2026-10-12)

## Context

A session outlives its runs: an input that wakes it on a schedule starts
a bounded run, and the run is held to a cap of its own. The budgets ([ADR 1006](1006-a-hold-is-held-before-the-call-and-written-once.md))
have no end: a budget is created, its amount changes, and it goes only
with its tenant. A cap left on the session's tree after the run counts
every later loop and parks the session once that spend passes it. A cap
made for each run piles up until a call is charged to more budgets than
the gate reads, which refuses it. No cap leaves the run unbounded.

## Decision

**A budget closes, once.** `close_budget` sets `closed_at` through the
same compare-and-set on the version that a change of the amount takes,
so a change read before the close is refused and never reopens it. A
close that loses to a change reads the budget again and closes it as it
stands. A closed budget answers a second close as stored. It takes the
permission that sets budgets, since it lifts a cap.

**The gate reads open budgets alone.** The read of a call's scopes
leaves a closed budget out, so it binds no call and never counts toward
the budgets one call may read. Its index holds open budgets alone, so a
scope's closed budgets never lengthen the read every call makes.

**A budget counts from its creation.** A tally is the budget's own, so
one made on a session's scope just before a run caps that run alone.
What it counted stays readable after its close.

**A close wakes the sessions parked on it.** Its commit lands a
`WAKE_SESSIONS` item that names the budget, and only the sessions whose
park names it are unlocked. A raise still wakes every session parked on
a budget. A run's close is frequent, and a wake writes to each session it
reaches, so a close reaches only the parks it can clear.

## Consequences

- A run's budget is made before the run's first input and closed when
  the run ends. A loop that must stop at the cap is cancelled before its
  budget closes; otherwise the woken loop goes on under its other
  budgets.
- A session parked on two budgets names one of them. When that one is a
  budget a person must raise, the close of the other leaves it parked
  until the raise, whose wake reaches every budget park.
- A closed budget stays a row of its tenant until the tenant's purge.
- The release before reads a closed budget as open, and refuses a wake
  that names a budget. Rolled back, it binds the closed budgets again
  until the roll forward.
