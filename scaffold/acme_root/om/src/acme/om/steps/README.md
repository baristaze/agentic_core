# Steps

The history of every agent session: one record per event, written once.
This is one of the kinds of thing [Acme is made of](../../../../README.md).

## What it holds

- **Step**: one event of a session, numbered in the session's order with
  no gaps. A request and its response are two steps, and the response
  names its request. A step that asks for a tool, or that asks a model,
  points at the steps it came from instead of copying them.
- **Type**: what the step records. An input (a person's message, or an
  event from outside), a control (pause, resume, cancel, interrupt,
  compact, approve, deny, unlock), a model's request and response, a
  tool's request and response, a summary, and the marks of a loop's
  life: parked, resumed, ended, switched, and the world changed.
- **Actor and origin**: who produced the step (a person, a program, an
  agent, the model, the engine, or something outside) and where it came
  in.
- **Principal and spender**: on whose authority a message, an event, or
  a tool call runs, and who pays for a call to a model. A person's or a
  program's message is written in the name of whoever appends it. A step
  an agent produced also names the agent: its kind and its session
  ([attribution](../attribution/README.md)).
- **Header and content**: the header is the step's shape, readable
  always: ids, names, counts, flags. The content is what was said: text,
  images, documents, the model's thinking, a tool's use and its result.
  An image or a document is held by reference; its bytes live in the
  store.
- **Stream part**: a live piece of a step still being written (a word of
  a model's answer, a fragment of a tool's input, a line a tool prints),
  numbered, and naming the step it adds up to. A part is never kept: the
  step is, once, whole, when its stream ends, and a stream that breaks
  still ends in a step marked cut short.
- **What a request read**: a model's request names the
  [window](../windows/README.md) it read (the model it was sized for, its
  first step, the summary before it), a hash of what it sent, keyed by
  the session, and the budget hold its worst case was reserved by. A
  response names why the model stopped. A tool's answer too large to keep
  in a step names the artifact that holds it whole, and keeps its head
  and its tail.
- **Cursor**: each session's last number, and the epoch of the run that
  holds it.

## What can happen

- **Arrive.** A message, an event, or a control lands in the session's
  history at once, whether or not the agent is working, so a sender's
  acknowledgement means it is kept.
- **Begin a run.** A run that takes up the session's loop takes the next
  epoch first. From that moment, a run that held an earlier one can
  write nothing more.
- **Record.** A run writes each step before it acts on it, under its
  epoch, and the steps take the session's next numbers.
- **Read** the history in order, a page at a time.

## The rules

- **A step is written once.** Nothing rewrites it or takes it out of the
  middle of a history: the database refuses both.
- **A history goes whole, and only by its purge.** When its session or
  its org is purged, every step goes, and its cursor last. The purge runs
  in the maintenance worker under a database login of its own, the one
  login that may delete a step.
- **No gap, no repeat.** Steps that arrive at once queue for the next
  numbers, and a number is never skipped or given twice.
- **A stale run is refused, never trusted to stop.**
- **The same step sent twice is kept once.**
- **A step is checked when it is made.** A step whose content does not
  fit its type, such as a response with no request, a tool call that
  copies what it calls, or a model call that names nobody to pay, is
  refused before it is kept.
- **Every step belongs to one org.** Another org that names a session
  finds no step of it.

## How another namespace composes it

The agent's loop takes the epoch, appends the steps it writes, and reads
the history to render what a model reads. A session's status is read off
its steps by [the agent sessions](../agent_sessions/README.md). The
inbox appends what arrives. Nothing else writes a step.
