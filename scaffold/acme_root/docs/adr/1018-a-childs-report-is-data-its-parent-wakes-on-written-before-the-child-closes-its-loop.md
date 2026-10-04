# ADR 1018: A child's report is data its parent wakes on, written before the child closes its loop

**Status**: accepted (2026-10-04)

## Context

The spec says a child's report reaches its parent's inbox, as data,
when the child's loop ends or when it needs a person, and that the
parent learns of each status change when it happens and never polls. A
report has a size bound, and above it the parent holds a preview and a
handle, as a large tool result does. It leaves four things open: what
the report holds, which reports wake the parent, how a report survives
a run lost at the loop's end, and where the bound comes from.

A report is the one path by which a child's words enter its parent. A
child's model may have read anything, so whatever crosses must stay
data, keep the mark, and never let a child restart a parent a person
stopped.

## Decision

**A report is read from the child's history.** When a child's loop ends,
the loop reads that loop's own steps, never the session's whole history,
and hands the agents manager what it finds: the outcome, the verdict of
the result gate when the loop ended on one, and the text of the loop's
latest complete response that said something. A park on a person, the
time a child needs one, is reported the same way. A park on the tree's
deadline, a budget, or a provider is not: the wait belongs to the tree,
and the parent shares it.

**A report is an agent's message, and data.** The agents manager writes
it into the parent's inbox as a message whose actor is the agent and
whose header names the child. It is never the parent's instruction. It
carries the child's mark as the child's history stands at that moment,
and, being data, it marks the parent too.

**Every report wakes the parent but one.** The note of a cancel that came
down from the parent does not wake it. That cancel is the parent's own
cascade, so the parent knows of it, and a parent whose loop a principal
cancelled must not start again on its children's word. A cancel a
person sends the child directly wakes the parent, which would otherwise
wait on a child that stopped.

**The end's report is written before the loop closes.** Its id is
derived from the loop. A run lost between the report and the closing
step leaves the loop open; the run that takes it up ends it again and
repeats the report, which the inbox keeps once. A note of a park is
written after the park, under an id of its own.

**The bound is the tool result's.** A report longer than the size bound a
tool result has is kept as an artifact of the parent's session, sealed
under its key and purged with its history. The parent's step holds the
report's head, its tail, and the artifact's handle, and its next request
renders them inside the data element with the notice of what is not
shown and where it is kept.

## Consequences

- A parent that delegates hears back without polling, and a child's
  words never instruct it.
- A parent that spawns a child is marked once the child reports, as it
  is by any data it reads.
- A crash at a child's loop end repeats its report at most once more,
  under the same id; a crash right after a park can lose that park's
  note, and the report of the loop's end still follows.
- A parent whose child a person cancels wakes and reads the note; a
  parent that cancelled its children reads their notes with whatever
  wakes it next.
