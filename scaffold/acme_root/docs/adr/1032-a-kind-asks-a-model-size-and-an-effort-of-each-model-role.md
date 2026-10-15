# ADR 1032: A kind asks a model size and an effort of each model role

**Status**: accepted (2026-10-11)

## Context

A call names a model role, and the resolver's table gives each role its
fill. A role the table does not name resolves to nothing, so every new
kind that adds a role needs a row decided for it, and a resolver keyed
on the kind needs rows for each new kind. A kind declared as data cannot
say how much model its roles need. A summarizer, an engineer's main
role, and a reviewer's main role need different amounts, and none of
them should name a model.

## Decision

**A size and an ask.** `ModelSize` runs from XS to XL. A kind's
`asks` hold, for a role it calls, a `ModelAsk`: the role, a size, and an
effort, which may be left out. A kind asks a role once, and only a role
it calls. A role it asks nothing of resolves by its name, as before.

**The resolver is given the asks.** `resolve` takes them beside the
roles and the eligibility, empty by default. The table resolver serves
an asked role from a second table, `sizes`, one row per size with its
fill and fallbacks; any other role from its own row. A size the table
does not map is `UnresolvedRole`. A resolver keyed otherwise, such as on
the kind, maps sizes as it maps roles. One that leaves the asks aside
still resolves every role by its name.

**An ask never widens what a fill allows.** A fill names the efforts
its model takes, `efforts`. It runs at an ask's effort only when it
names it; otherwise at its own. The rule holds for each fallback too, so
a fallback carries the effort it may run at. A fill that names no
efforts never moves: it keeps its effort under any ask. What a fill allows sits on the fill, not on its
eligibility: eligibility is what a session requires of every fill, and
a session requires no effort.

**The fill set keeps the ask.** Each role's fill keeps the ask it was
resolved from, beside what it got, so a reader sees an effort asked and
not granted. A switch keeps it.

**The default sizes.** S runs on what the summarizer runs on and L on
what the main role runs on, so a kind that asks those sizes gets the
models a kind that asks nothing gets. XS is S with a shorter answer, M
is L at medium effort, and XL is the largest model of each provider.
`claude-haiku-4-5` takes no effort level, so the XS and S fills keep
their own and their fallbacks take the ask's.

## Consequences

- A kind can add a role as data and get a fitting model, with no row
  for the role and no change to a resolver.
- A session's fills are still resolved once, so an ask changes nothing
  mid-session. A kind version that asks differently reaches only new
  sessions.
- A stored fill set that holds no ask reads with none, and its fills
  with no efforts.
- A product that maps sizes to its own models names, for each fill, the
  efforts its model takes. Until it does, an ask's effort moves nothing.
