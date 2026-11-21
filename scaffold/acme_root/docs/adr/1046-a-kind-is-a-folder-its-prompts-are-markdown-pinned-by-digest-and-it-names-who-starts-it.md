# ADR 1046: A kind is a folder, its prompts are Markdown pinned by digest, and it names who starts it

**Status**: accepted (2026-11-21)

## Context

A kind's prompts are strings in its module. A person who reads or edits
one reads Python to do it, and a prompt split over concatenated literals
hides its exact text. Nothing holds a released version's text. An edit
to a version's prompt changes what every session pinned to that version
renders, mid-life, though a session pins its kind version so that its
instructions never change under it (Agent Kinds). The prompt cache keys
on those bytes too.

Who may start a kind is read from its other fields. A person may start
any kind, and a spawn any kind that names a share and runs in a worker.
A share says what a sub-agent may spend, not whether one should start.
So a kind written for a person runs as a sub-agent once it names a
share, and a person may start a kind written only for a parent.

## Decision

**A kind is a folder.** A product keeps each kind in a folder of its
own, `kinds/<kind>/`, beside the others and never inside one. A module
at its root holds the kind's `AgentKind` constants, one per version,
with every setting a Python symbol, and `VERSIONS`, every version the
product still runs. Beside it, `prompts/v<N>.md` holds the prompt of
version N in Markdown. The constant reads it at import
(`prompt_of(__file__, N)`), so the kind renders the file's text, less
the line break that ends it, and no prompt is a string in code. A
version's prompt is one text. The engine reads the module and
`prompts/` alone; a layer above may keep folders of its own beside
them.

**A released prompt is pinned.** `prompts/lock.json` pins each released
version's prompt by the SHA-256 of its text. An entry is written once,
when the version's text is final, and it is never changed or removed,
even after its version retires.

**A check refuses what breaks it.** `prompt_breaches` reads a folder
with its module's `VERSIONS` and names the file of each breach: a
version's prompt file missing, not UTF-8, or past 24,000 characters,
the standing text's bound, since it too renders whole on every request;
a version whose prompts are not its file's text; a version the lock
pins no digest for, or another digest, which is a released prompt
changed in place; a file under `prompts/` that no version reads; a
version of another kind than the folder's. A product's tests run it over
every kind folder it keeps, so its gate refuses each breach. A missing
file, or one past its bound, also refuses the import.

**A kind names who may start it.** `AgentKind.started_by` is `person`,
`agent`, or both; a kind that names neither is started by a person
alone. A person's start, and a hand-off, whose principal confirms its
objective before it starts, need `person`. A spawn needs `agent`, and a
share as before. A start the kind does not allow is refused before
anything is made, naming the starter it allows. A request kind that
names `agent` is refused when its catalog is built, with ADR 1044's
other breaches.

A parent may start any kind that names `agent`. A child's tools are cut
to its parent's, and each of its calls is decided under every kind above
it, so a list of the kinds each parent may start would bound no power
the cut and the gate leave.

## Consequences

- A session pinned to a version renders the text that version was
  released with, and every session of the version keeps one cached
  prefix.
- A change to a released prompt is a new version: a file, a constant,
  and a lock entry of its own. Sessions on the old version keep its
  text.
- A version that composed several prompts renders one text once it
  moves to its file, and its cached prefix is written again once.
- A kind a spawn starts names `agent` beside its share. One that names
  only its share is refused as a sub-agent, and a person cannot start a
  kind that names `agent` alone.
- A kind's prompt has a bound: one past 24,000 characters is refused.
