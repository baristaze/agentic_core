# Models

Which model does each job of an agent session, and how that changes. This
is one of the kinds of thing [Acme is made of](../../../../README.md).

## What it holds

- **Model role**: a job a call names, never a model: the agent's own
  turns (`main`), the summary that keeps a long session readable
  (`summarizer`), or one a product adds, such as a title.
- **Fill**: what does a model role's job: a provider, a model, how hard it
  works, how long an answer may be, the shape of the answer, how much it
  reads at once, and what it offers, such as keeping no data or running
  in one region.
- **Fill set**: a session's fills, one per model role, with the fallbacks
  each declares, in order. It has versions: the first is the session's,
  and each switch makes the next.
- **Resolver**: what picks a session's fills, from a table a product
  sets. The engine never picks a model itself.
- **Price**: what a model's tokens cost at list price. Every model the
  resolver can pick has one, kept with the budgets.

## What can happen

- **Resolve.** A session's model roles get their fills once, within what
  the session requires, and keep them.
- **Switch.** A fill changes when its provider fails and a declared
  fallback takes over, when a model is retired or a better one comes, or
  when a policy says so. The history records the switch, naming both
  fills, and the fill set takes its next version.
- **Fall back.** The next declared fallback the session may run on, and
  no call has tried yet, takes the role. When none is left, the loop
  waits.
- **Settle.** A switch the history recorded and a crash left unwritten is
  written from the history.

## The rules

- **A call names a model role, never a model.**
- **A switch is never silent.** Each one is a step in the history first,
  then a new version of the fill set. No version comes without its step,
  and none is rewritten.
- **A run that lost its claim switches nothing.**
- **No model without its price.** The resolver refuses a model with no
  price, so no budget is built on a guess.
- **A fallback stays within the session's requirements**, such as keeping
  no data or a region.
- **Every fill set belongs to one org.** Another org finds none, and a
  deleted org's go with its sessions.

## How another namespace composes it

The agent's loop resolves a session's fills when it first needs one,
reads the fill for each call, and switches when a provider fails or a
model is gone. A switch writes its step through [the
steps](../steps/README.md). The calls themselves go to the model
providers, under `integrations/`, which read a provider's failure into
the kind that decides what the loop does.
