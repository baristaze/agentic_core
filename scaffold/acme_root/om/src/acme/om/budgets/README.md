# Budgets

What an agent may spend, and the one check every model call and every
job that spends passes before it starts. This is one of the kinds of
thing [Acme is made of](../../../../README.md).

## What it holds

- **Budget**: a scope, a window, and an amount. The scope is a key the
  platform gives: a session, a tree of sessions, a person, a project, a
  team, or the whole org. The window is the scope's whole life, an hour,
  a day, a week, a month, or a span of seconds, counted in UTC. The
  amount is reference cost, native tokens, or both.
- **Reference cost**: what a call costs at list price, whoever pays, in
  millionths of the reference currency. A budget reads it, so one
  workload meets one line whether the platform's key or the org's own
  pays.
- **Hold**: a call's worst case, reserved on every budget of its scopes
  before the call. The worst case is the prompt at the highest rate that
  can apply, the whole output bound, thinking billed beyond it, and the
  fees of the provider's own tools with the input they may add, which can
  carry a call past a long-context threshold. A job's is its rate until its
  deadline.
- **Refusal**: the answer when a call does not fit. It lists every
  budget it breaches, each with the one action that clears it and when
  its window resets.
- **Settlement**: how a hold closes, once the call is over.
- **Ledger**: the holds, the settlements, and a tally per budget and
  window of what is held and what was spent.
- **Price**: a model's list rates, from one versioned table: input,
  output, a cache read, a cache write, a longer-lived cache's write where
  the provider keeps one, the rates past a long-context threshold, and
  the fee of each tool the provider runs itself. A model with no row has
  no price; there is no default row.

## What can happen

- **Set a budget**, or change its amount. Either takes the permission
  that governs the org's members. A raise lets the sessions parked on a
  budget go on: each of them is woken, and asks again.
- **Cap a session.** A budget on one session over its life, as a spawn
  gives its child its share. It takes only the permission to write: it
  narrows what the session may spend, and every other budget of its
  calls still binds.
- **Authorize a call.** The gate holds its worst case on every budget at
  once, or refuses it and holds nothing.
- **Settle a hold.** It is released only when the provider provably did
  not bill. Otherwise it counts the usage the provider reported, or
  usage retrieved later, else the whole hold: a broken stream or a crash
  after the call was sent is usually billed.
- **Read a budget's spend** in its current window.

## The rules

- **One gate, before the call.** A check after the call overshoots every
  stop by one call. Compaction and every side task pass it too. A gate
  that holds nothing is for a developer's machine: a deployed process
  refuses it when it starts.
- **Nothing is spent when no one pays.** A call whose payer is unknown
  is refused.
- **Two calls never both fit where one does.** Holds over one budget
  queue, so two sessions cannot slip under one line in the same second.
- **A token budget still binds** when a model has no price or is free.
  A budget that bounds cost refuses a call no price can bound.
- **A spend past its hold is counted and alarmed,** never absorbed.
- **What a call held and spent is written once.** Nothing rewrites or
  removes a hold or a settlement.
- **Every budget, hold, and settlement belongs to one org.** Another
  org's answers as one that never existed.

## How another namespace composes it

The loop asks the gate before each model call and each spending job,
with the scopes the call is charged to and its worst case
(`rules.call_exposure`, `rules.job_exposure`, over a price read through
`pricing.PricingInterface`). A refusal parks the loop on the budget
(`rules.budget_park`) in [the agent sessions](../agent_sessions/README.md),
which wake it at the reset or on a raise. The ledger keeps its own
tables (ADR 1006).
