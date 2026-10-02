# Windows

What a model reads of an agent session. This is one of the kinds of
thing [Acme is made of](../../../../README.md).

A session's history only grows, and a model reads a bounded amount. A
window is the view between the two: the part of the history one request
reads, sized for the model that reads it.

## What it holds

- **Request**: what one call of a model sends, rendered from the
  session's [steps](../steps/README.md). It is laid out from what changes
  least to what changes most: the agent kind's prompts and its tools, the
  pinned zone, the latest summary, the steps since it, the inputs the
  agent has not read yet, and its current plan last. So each request
  starts with the one before it, and the provider can reuse what it read.
- **Window**: the steps one request reads, by reference: the model it is
  sized for, how much it holds, and its first and last steps. A window
  belongs to a request, never to the history; it is rebuilt from the
  steps whenever it is needed.
- **Pinned zone**: what every request carries however much is folded: the
  session's objective and the standing instructions its principals gave,
  quoted from their own messages, or, past a bound, cited.
- **Summary**: a step that stands for a range of the history when a model
  reads it. The model that wrote it, the summarizer, is answered like any
  other call, and the summary points at that answer.
- **Artifact**: a tool's result too large for a step, kept whole in the
  object store, with a record of it beside the history. The step keeps
  its head, its tail, and the artifact's handle, and the agent reads the
  rest a page at a time.

## What can happen

- **Render.** The next request of a model role is drawn from the history.
  The agent's own model reads the active window: everything since the
  latest summary. A side task, such as a title, reads the latest steps
  its own smaller model holds.
- **Compact.** When the active window nears its limit, a person asks for
  it, or the agent's model moves to a smaller one, the oldest part is
  folded into a summary before the next request. The latest exchange
  stays as it was, and so does any input the agent has not read.
- **Retry once.** When a provider refuses a request as too long, the
  window compacts and the request is sent again, once.
- **Keep a large result.** A tool result above the size bound is kept as
  an artifact before its step is written.
- **Read an artifact**, a page at a time.

## The rules

- **The same steps render the same request,** byte for byte, and its hash
  is recorded with it, so a request can be rendered again and checked.
- **No window separates a pair:** a tool's call from its answer, nor a
  message from the reply that read it.
- **Only a principal instructs.** The pinned zone quotes principals'
  messages alone. A summary, an event, a tool's output, and anyone else's
  words are read as data, marked as such, whatever they say.
- **Compaction changes what is read, never what is kept.** No step is
  rewritten or removed; a summary is one more step.
- **A summary is a model call like any other:** it passes the budget
  gate before it starts, and a run that lost its claim writes nothing.
- **Compaction never loops.** A request compacts at most once before it
  is sent, and once more if the provider refuses it as too long; a
  second refusal ends the loop. A summary the summarizer refuses or cuts
  short is recorded, and the window is read as it is while it fits; the
  summarizer is not asked again before the next summary unless the window
  fits no more or a person asks.
- **An artifact is written once,** and belongs to one org and one
  session: another org, or another session, finds none.

## How another namespace composes it

The agent's loop asks for each request here, records it as a step, and
calls the model it names. It keeps each large tool result here before it
writes the result's step. The fills come from [the
models](../models/README.md), the steps from [the
steps](../steps/README.md), and the summarizer's call goes to the model
providers, under `integrations/`, behind the budget gate.

<!-- agents-only
- The pure rules are `rules.py`: `consistent_cuts`, `pinned_zone`,
  `render_main`, `render_side`, `fold_cut`, `summarizer_call`,
  `request_step`, `prompt_bytes`. The manager reads the history and the
  fill set, and appends a compaction's steps under the run's epoch.
- The gate (`gate.py`) and the keyed hash (`hashes.py`) are narrow
  interfaces a root wires to the budgets' gate and the key service; the
  root's defaults are loud nulls that refuse.
- A summary step holds no text: it references the summarizer's response,
  which holds it (ADR 1008).
-->
