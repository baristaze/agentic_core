# ADR 1006: A hold is held before the call, and written once

**Status**: accepted (2026-10-02)

## Context

Every model call and every job that spends passes one gate before it
starts. The gate holds the call's worst case on every budget the call is
charged to, or refuses it with every breach. A check after the call
overshoots every stop by one call, and a check that reads a tally and
writes it in two steps lets two sessions slip under one line in the same
second.

A budget is the system of record: a person sets it and changes it. What
the calls held and spent is a ledger, which the guideline's roles put in
`activity`, beside the other append-only streams. Nothing crosses a role,
so one transaction cannot read a budget in `core` and move a tally in
`activity`.

## Decision

**The budgets are `core`; the ledger is `activity`.** A budget is a row
with its scope, its window, and its amount, written with its outbox rows
and changed by a compare-and-set on its version. The gate reads the
budgets of a call's scopes, and the hold carries each one's amount as it
read it. The ledger then decides on the hold alone, so it never reads
`core`. A change that lands between the read and the hold counts from the
next call.

**A tally per budget and window is the lock.** The hold's one transaction
makes the tally of each line where its window has none, locks the tallies
in one order (by budget, then window), holds the call against what each
window spent and holds already (`budgets.rules.breaches`), and either adds
the call's worst case to every line and writes the hold, or writes
nothing and answers every breach. Two holds over one line queue on its
tally, so they never both pass when only one fits, and two calls over the
same lines never wait on each other crosswise.

**A hold and its settlement are written once.** The serving logins keep
SELECT and INSERT on `budget_holds` and `budget_settlements` and lose
UPDATE and DELETE, as on the history (ADR 1002). A settlement is unique
per hold and moves the tallies in its own transaction, under the same
locks: the hold leaves what the window holds and what it spent joins what
the window spent. A second settlement of one hold answers the first.

**A hold is released only on proof.** A settlement names its bill: not
billed, with the proof that the provider never processed the call;
billed, with the usage reported or retrieved later; or unknown, which
counts the whole hold. A spend past the hold is counted in full and
logged as an error.

**A refused loop parks on the budget, and wakes by itself.** Each breach
clears one of three ways. Room that open holds take frees when they
settle, so where what the window spent leaves room for the call, the park
tries again soon, never after the reset. A window's reset clears a call
the line's amount holds at all. A call whose worst case alone passes the
amount, a window that never resets, or a missing price waits on a
person's raise or price: no retry time. The park tries again when the last
of its breaches can have cleared, and names the breach that binds
longest. A park with a retry time lands, with the session's write, a
`WAKE_SESSION` work item that waits in the queue until then. A raised budget lands a `WAKE_SESSIONS` item that wakes every
session of the org parked on a budget. Each wake writes the engine's
`unlock` control, and the run that takes the session up asks its gates
again.

## Consequences

- No statement a process sends rewrites what a call held or spent.
- A tally can be rebuilt from the holds and settlements of its window.
- An open hold counts until it is settled. A run that crashed after its
  call leaves it open, and the run that recovers the call settles it.
- A wake for a budget wakes the sessions parked on any budget of the
  org. One whose budget is still short asks the gate, is refused, and
  parks again.
- The ledger grows with every call. Its retention is a decision of its
  own, made when a tenant's ledger is first purged.
