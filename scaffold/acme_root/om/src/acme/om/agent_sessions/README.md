# Agent sessions

Conversations with an agent that never end: a series of loops over one
history. This is one of the kinds of thing [Acme is made of](../../../../README.md).

## What it holds

- **Agent session**: one conversation with an agent: who started it,
  its title, the people it is shared with, its [agent
  kind](../agents/README.md) and the tools it may call, the session that
  spawned it or handed it the work, the root of its tree, and a status it
  keeps up to date from its [steps](../steps/README.md). The steps are
  the truth; the status is kept for listing and finding, and so are the
  person who spoke last and the untrusted mark
  ([attribution](../attribution/README.md)).
- **Loop**: the steps from what woke the session to how the loop ended.
  It is no record of its own: a loop is a span of steps.
- **Status**: `pending` while an input waits for the agent, `running`
  while the agent works, `parked` while a loop waits on something, and
  `idle` when no loop is open.
- **Park**: why a parked loop waits, what clears it, and when it tries
  again by itself.
- **Archived**: a flag a person sets on an idle session.

## What can happen

- **Start** a session. It begins idle, with no history.
- **Wake.** An input that wakes an idle session makes it pending, and a
  loop begins.
- **Work.** Once the agent writes a step, the session is running.
- **Park.** A loop that cannot go on yet parks; what clears it makes the
  session pending again, for the agent to take up.
- **End a loop.** The session goes idle, and the next input that wakes
  it starts the next loop over the same history.
- **Archive.** An archived session keeps what arrives and wakes for
  nothing, until a person's message brings it back.
- **List** the sessions in a status, a page at a time.

## The rules

- **A session never ends.** A loop ends; the session waits for its next
  input.
- **The status follows the steps.** It is read off the history, can be
  rebuilt from it at any time, and is never set by hand.
- **A change of status, and the end of a loop, are announced.** A step
  on its own is not.
- **Two writers never both land.** Each write of a session names the
  version it read.
- **A session takes what it must from where it came.** A sub-agent or
  a handed-over session carries the mark of the session it came from,
  and a sub-agent calls only tools its parent may. Whoever makes it
  cannot choose otherwise.
- **Every session belongs to one org.** Another org's session answers as
  one that never existed, and a session's parent is in its own org.
