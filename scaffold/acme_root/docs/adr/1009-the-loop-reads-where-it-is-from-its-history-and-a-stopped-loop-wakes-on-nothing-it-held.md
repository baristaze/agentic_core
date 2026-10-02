# ADR 1009: The loop reads where it is from its history, and a stopped loop wakes on nothing it held

**Status**: accepted (2026-10-02)

## Context

One loop drives every namespace of the engine: it renders a request,
passes the budget gate, calls the model, and runs the calls the model asks
for. A run of it can be lost at any await, and a new run must take the
loop up without repeating what a repeat would harm. A run that lost its
claim may still be running: its process does not know.

A loop ends `errored` when an error no park can clear stops it, and
`cancelled` when a principal cancels it. An input that woke the session
and was never delivered keeps the session pending, so a new loop starts on
it. A kind that ends its loops through a result tool nudges a turn that
called no tool. A provider whose calls fail fast fails them for every
session on the same key.

## Decision

**The history says where the loop is.** A run keeps nothing a later run
would need. It takes a writer epoch first, then reads its loop's steps:
the latest complete response with a call still unanswered is settled
before any model call, and a turn no step acted on is judged by the
kind's done rule. A run that finds a model request with no response
closes it as abandoned and settles the hold the request names at the
whole of it, since a call that was sent is usually billed. A call that a
run wrote, or one a park held back, runs; any other was in flight when a
run was lost, and is settled by its effect.

**A run's answer is its own.** A tool response's id is derived from its
request and the run's epoch. Two runs never write the same id, so a run
that lost its claim never lands its answer as the one a later run wrote,
and its next append is refused. The parts a call's output streams carry
that id before the response is written.

**A stopped loop wakes on nothing it held.** A loop that ends `errored`
or `cancelled` leaves the session idle, whatever input it left
undelivered. The input waits, and the next request delivers it with
whatever wakes the session next. A loop that ends any other way leaves an
undelivered waking input pending, and a new loop starts on it.

**The engine's notice is a message only a run writes.** A nudge, and the
notice that a turn was paused, are messages the engine writes under the
run's epoch, on the authority the session's calls run under. They
instruct, wake nothing, never pay, and never feed the pinned zone. The
inbox refuses an input the engine wrote. The next request carries the
notice between the model's two turns.

**A known outage parks.** When the in-process retries of an error worth
retrying are spent, the loop reports an outage of that provider for that
credential until a retry time, then falls back to its next declared
fallback, or parks on the provider. A run that finds an outage known for
its main fill parks at once, naming the provider, before anything is
rendered or held.

## Consequences

- A crash at any await leaves at most one unanswered request per call,
  and a lost hold settled in full rather than held forever. A hold opened
  in the moment before its request was written is not found.
- An unsafe call a lost run may have made is answered from the
  transport's record, or `interrupted`; the model verifies before it
  retries.
- A principal who wants a stopped loop's input acted on writes again; an
  error that met an input never meets it again by itself.
- A session whose main fill's provider is known to be failing waits for
  its retry time even when it declares a fallback another session
  already reached.
