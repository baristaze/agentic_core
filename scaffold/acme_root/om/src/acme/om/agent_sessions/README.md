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
  again by itself. A park only a person clears (a question, a hand-over,
  a pause) has no time to try again.
- **Limits**: what keeps a loop from running forever. A deadline the
  whole tree shares and a step guard, which counts model calls in one
  loop, park it for a person; a streak of tool errors or of identical
  calls, and too many turns that neither go on nor finish, end it
  `inconclusive`; a run that has run long hands its loop to the next run.
- **Archived**: a flag a person sets on an idle session.

## What can happen

- **Start** a session. It begins idle, with no history.
- **Wake.** An input that wakes an idle session makes it pending, and a
  loop begins.
- **Work.** Once the agent writes a step, the session is running.
- **Park.** A loop that cannot go on yet parks; what clears it makes the
  session pending again, for the agent to take up.
- **Wake by itself.** A park with a time to try again is woken at that
  time from the work queue, and a raised budget wakes every session
  parked on a budget. A woken session is pending; the run that takes it
  up writes a `resumed` step and asks its gates again.
- **End a loop.** The session goes idle, and the next input that wakes
  it starts the next loop over the same history.
- **Archive.** An archived session keeps what arrives and wakes for
  nothing, until a person's message brings it back.
- **List** the sessions in a status, a page at a time.

## The rules

- **A session never ends.** A loop ends; the session waits for its next
  input.
- **"Not now" is not "failed".** A park writes no outcome, and a limit
  that no raise cures ends the loop `inconclusive`, never `failed`, and
  never the session.
- **The step guard is never off.** Every loop starts it afresh, and a
  person's look after it parks starts it again.
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
