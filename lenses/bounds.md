# Bounds

Group id: `bounds`. Covers Bounds and Budgets and Parking of
`agentic_core_spec.md`.

This group judges the limits an agent runs inside and what happens when
one trips: budgets, the one gate and its hold, breaches, usage and cost,
the guards, bounds, and yields, and parking. It leaves the budget a tree
shares to `agents`, a fill on a tenant's own key to `models`, the null
gate a root refuses to `privacy`, and the outcome a bound writes to
`steps`.

## BND-01 A budget has a scope, a window, and an amount

**Principle.** A budget has a scope, a window, and an amount. The scope
is a session, a tree, a person, a project, a team, or a tenant, and the
engine treats scopes as keys the platform defines. The window is the
scope's whole life, an hour, a day, a week, a month, or a custom span
down to the second, and a single request may carry its own. The amount
is in reference cost, in native tokens, or both. A token budget still
binds when a price is unknown or a model is free.

**Source.** Bounds and Budgets, Budgets.

**Look for.** The budget type; how scopes are keyed; the window
arithmetic; how a token amount binds.

**Violation.** A scope the engine interprets beyond a key; a window that
cannot express a custom span; a token budget skipped when a price is
missing.

**Severity.** medium

**Shape.** `scaffold/acme_root/om/src/acme/om/budgets/types/budget.py`

**Check.** review

## BND-02 Every model call and every spending job passes one gate first

**Principle.** Every model call passes one gate, compaction and side
model roles included, and so does every job that spends. Before the
call, the engine asks the gate to authorize the call's scopes against
its worst-case exposure, and the gate answers with a hold or a refusal
listing every breach; after the response, the engine records usage and
settles the hold. A check after the call overshoots every stop by one
call. The engine fails closed for spend: when it cannot tell who pays,
nothing is spent.

**Source.** Bounds and Budgets, One Gate, Before the Call; Breaches and
Failing Closed.

**Look for.** Every path that calls a provider or starts a spending job,
and the gate's authorization before it; what the engine does with a
refusal and with an unknown spender.

**Violation.** A model call, a compaction, a side role's call, or a
spending job with no authorization before it; a budget checked only
after the call; a call made when the spender is unknown.

**Severity.** high

**Shape.** `scaffold/acme_root/om/src/acme/om/budgets/gate.py` and
`scaffold/acme_root/om/src/acme/om/windows/impl/manager.py`

**Check.** review

## BND-03 The hold covers the worst case

**Principle.** Only the engine knows a call's worst-case exposure, and
the hold covers it: the prompt at the highest input rate that can apply,
the output bound at the highest output rate, any thinking billed outside
that bound, and the fees of the provider's own tools. The highest rate
includes a cache write when the request writes cache, and a long-context
tier, which raises output rates too, when the prompt can cross it. The
prompt's size is the provider's count or a proven upper bound, never a
local estimate. A job's worst case is its rate times its deadline.

**Source.** Bounds and Budgets, One Gate, Before the Call.

**Look for.** The exposure computation and the rates it reads; the
source of the prompt's size; the exposure of a job.

**Violation.** An exposure at the base input rate when the request
writes cache or can cross a tier; a prompt size from a local estimate;
thinking or a provider tool's fee left out; a job held for less than its
rate times its deadline.

**Severity.** high

**Shape.** `scaffold/acme_root/om/src/acme/om/budgets/rules.py` and
`scaffold/acme_root/om/src/acme/om/budgets/types/exposure.py`

**Check.** review

## BND-04 A hold settles unless the provider provably did not bill

**Principle.** A hold is released only when the provider provably did
not bill, as when it refused before processing. Otherwise it settles at
the reported usage, or at usage retrieved later, else at the full hold:
a broken stream or a crash after the call was sent is usually billed.
Adapters normalize usage into disjoint classes, since providers differ
on whether cached and reasoning tokens are subsets of their other
counts, and thinking is never counted from visible text. An overshoot
past the hold is recorded and alarmed, never absorbed.

**Source.** Bounds and Budgets, One Gate, Before the Call.

**Look for.** Every path that releases or settles a hold; what a broken
stream or a crash after the send settles at; how usage classes are
normalized and thinking is counted; what an overshoot does.

**Violation.** A hold released on any error; a crash after the send
settled at zero; cached tokens counted twice or not at all; thinking
counted from the returned text; an overshoot absorbed without a record
or an alarm.

**Severity.** high

**Shape.** `scaffold/acme_root/om/src/acme/om/budgets/types/hold.py` and
`scaffold/acme_root/integrations/src/acme/integrations/model_providers/types.py`

**Check.** review

## BND-05 A refusal names every breach

**Principle.** A refusal reports every budget it breaches, not only the
first, because clearing one would reveal the next. Each breach names the
one action that clears it and when its window resets. The engine's own
ledger counts in process; a platform supplies a shared one, so a window
counts across sessions. Whether a tenant prepays or postpays changes
what the ledger draws from, never the gate.

**Source.** Bounds and Budgets, Breaches and Failing Closed.

**Look for.** The refusal type and how it is built; the ledger interface
and its impls; any branch on how a tenant pays.

**Violation.** A refusal that stops at the first breach; a breach with
no clearing action or no reset time; a gate that branches on prepay or
postpay.

**Severity.** medium

**Shape.** `scaffold/acme_root/om/src/acme/om/budgets/types/breach.py` and
`scaffold/acme_root/om/src/acme/om/budgets/rules.py`

**Check.** review

## BND-06 Every call records native usage and reference cost

**Principle.** The engine records two figures on every model call and
never conflates them: native usage, tokens by class (input, cache read,
cache write, output, thinking), and reference cost, what that usage
costs at list price, whoever paid. Budgets read reference cost, so one
workload meets one line whether the platform's key or the tenant's own
pays, and a tenant's own key changes who pays, never what is gated. A
pricing interface supplies prices from one source, and every model a
resolver can pick has a price of its own, never a default row.

**Source.** Bounds and Budgets, Usage and Cost; The Tenant's Own Key.

**Look for.** The usage and cost fields of a model call; the price
source and its lookup; what a model with no price row gets.

**Violation.** One number standing for both figures; a budget read in a
provider's native tokens when it is set in reference cost; a default
price row for an unknown model (the hold it under-covers is BND-03); a
call on the tenant's key priced or gated differently from one on the
platform's.

**Severity.** medium

**Shape.** `scaffold/acme_root/om/src/acme/om/budgets/types/amount.py` and
`scaffold/acme_root/om/src/acme/om/budgets/pricing.py`

**Check.** review

## BND-07 A guard parks, a bound ends the loop, a yield hands it on

**Principle.** Each limit is a guard, a bound, or a yield. A budget is a
guard: it parks until it is raised or its window resets. The deadline,
one instant the tree shares, is a guard: no model call or job starts
after it, a tool's timeout is cut to it, and the loop parks for a
person. An error streak, of consecutive tool errors or repeated
identical calls, and repeated nudges are bounds that end the loop
`inconclusive`, never `failed`, and never end the session. Run time is a
yield: the run persists and hands its loop back to the queue. The tree's
deadline is the session's, never `ctx.deadline`.

**Source.** Bounds and Budgets, Time, Steps, and Streaks; Parking.

**Look for.** Each limit and what it does when it trips; where the
deadline is read; the yield on run time.

**Violation.** A budget or a deadline that ends the loop instead of
parking it; a bound that writes `failed` or ends the session; a deadline
read from `ctx.deadline`; a run that outlives its run time instead of
yielding.

**Severity.** medium

**Shape.** `scaffold/acme_root/om/src/acme/om/agent_sessions/limits.py`

**Check.** review

## BND-08 The step guard is never disabled

**Principle.** The step guard counts model calls in one loop and parks
so a person looks. It is on by default and never disabled: it is the one
backstop a free model or a missing price cannot switch off. Every loop
starts it afresh, and a step count is never a lifetime cap, which would
fail every message to a long session, forever.

**Source.** Bounds and Budgets, Time, Steps, and Streaks.

**Look for.** The step guard's setting, and whether a kind or a tenant
can turn it off; where its count resets.

**Violation.** A setting that disables the guard or makes it unbounded;
a count kept across loops; a guard that ends the loop instead of parking
it.

**Severity.** medium

**Shape.** `scaffold/acme_root/om/src/acme/om/agent_sessions/limits.py`

**Check.** review

## BND-09 "Not now" is not "failed": a park names its reason

**Principle.** A loop parks when it cannot continue yet. A park names
its reason (a person, a provider, a budget, a resource, a job, children,
a hand-over, or a pause) and the one action that clears it. A parked
step writes no outcome; it carries its reason, its unlock, and its
retry time, and no retry time means only a person can unblock it.
Treating "cannot continue right now" as "this did not work" throws away
a long conversation and its evidence.

**Source.** Parking.

**Look for.** Every path that stops a loop for something it waits on;
the reason, the unlock, and the retry time a park records.

**Violation.** An outage, a budget refusal, or a missing answer that
ends the loop `errored` or `failed`; a park with no reason or no unlock;
a retry time on a park only a person can clear.

**Severity.** medium

**Shape.** `scaffold/acme_root/om/src/acme/om/steps/types/header.py`

**Check.** review

## BND-10 A park holds nothing, and a woken loop is not trusted

**Principle.** A park holds no runtime and no work lease while it waits,
beyond what it declares, such as a memory-only session's runtime. When
the unlock happens, a resumed step starts a new run, by itself where the
reason unlocks without a person. Raising a budget is the instruction to
continue. A woken loop is not trusted: its gates run again.

**Source.** Parking.

**Look for.** What a parked loop keeps; the wake path of each reason;
the checks a resumed run makes.

**Violation.** A parked loop that keeps its runtime or its lease without
declaring it; a park that waits for someone to restart it after its
unlock happened; a resumed run that skips its gates. (A model call it
makes without the budget gate is BND-02.)

**Severity.** medium

**Shape.** `scaffold/acme_root/om/src/acme/om/agent_sessions/manager.py`

**Check.** review
