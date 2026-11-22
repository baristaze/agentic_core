# ADR 1047: A response's calls run one after another, in the order the model made them

**Status**: accepted (2026-11-22)

## Context

A model may ask for several tool calls in one response. Each is its own
request and response pair. The loop can run them one after another, or
at once.

The calls of one response share what they touch. Each reaches the
session's one workspace, where two commands may write the same file, or
one may read what the other is still writing. Each streams its output
to the session's one stream while it runs. A call that waits for a
person parks the loop, and a loop holds one park.

Running calls at once is the last step of the guideline's order for
fixing calls, after making fewer of them. What it would save is a
reading. `performance/shapes/long-session.json` holds the shape of one
long session: 1,118 model turns and 1,240 tool calls, with each call's
time. 240 of its responses ask for more than one call. With a spawn
counted at no time, since it returns at once:

- Every call of a response run at once would save 5.2% of the session's
  model and tool time.
- The calls that cannot conflict, reads and integrations on distinct
  targets, would save under 1%. The shape has no response of reads
  alone.

## Decision

**The calls of a response run one after another, in the order the model
made them.** Every request is persisted first, in one append, before
any call is decided. Then each call in turn is gated, run or recovered,
and answered (`LoopToolTurn._tool_turn`). A call that waits for a
person parks the loop once the others are answered, so the loop parks
once for all of them. A call that stops the loop, cancels it, or parks
it for anything else ends the turn there. The calls after it stay open,
and the run that takes the loop up settles them, in the same order.

**Nothing runs two calls of one response at once.** So nothing needs a
rule for two commands in one workspace, a stream that interleaves two
calls' output, or a cancel that finds every running call.

## Consequences

- A response's calls take the sum of their times. On the shape, that
  costs 5.2% of the session's model and tool time.
- Two commands of one response never race in their workspace: the
  second sees what the first left.
- A person who watches reads one call's output, then the next, and
  decides every call that asks at one park.
- A captured session on which the calls that cannot conflict would save
  3% or more of its model and tool time opens this decision again.
