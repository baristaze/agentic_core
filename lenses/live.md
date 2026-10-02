# Live

Group id: `live`. Covers Streams and Steering of `agentic_core_spec.md`.

This group judges what happens while a loop runs: what streams, how an
input reaches a running session, how a control cuts in, and how a person
takes over. It leaves the step a stream adds up to and its recovery to
`steps`, the trace of a delivered input to `trust`, approvals to
`tools`, and the parks a pause or a hand-over writes to `bounds`.

## LIV-01 Everything agentic streams, and emission never waits

**Principle.** An agent streams by default. Model output streams as it
arrives: text, thinking, and tool-call arguments. Tool output streams
while the tool runs. Artifacts a tool produces stream as ordered parts:
opened, appended, and completed with a final size and hash. Every
sub-agent streams too, so a viewer can follow a whole tree. Emission
never blocks the loop: a slow viewer is the carrier's problem, never the
agent's.

**Source.** Streams.

**Look for.** The stream sink and where the loop emits to it; whether an
emit can wait on a viewer; the parts of an artifact and its completion.

**Violation.** A response emitted only once it is whole; an emit that
awaits a viewer's acknowledgement or a full buffer; an artifact
completed with no size or no hash.

**Severity.** medium

**Check.** review

## LIV-02 A part is never a step, and a response collects its stream

**Principle.** A stream part is typed (a text delta, a thinking delta, a
chunk of tool output, an artifact part), numbered within its stream, and
carries the id of the step it will add up to. A part is never stored as
a step or an event. Text parts add up to the step stored when the stream
ends: a response is saved once, whole, and a stream that breaks still
ends in a step, marked truncated, holding what arrived. An artifact's
bytes are its content, written as they arrive. A whole response is a
collector over the stream, never the other way around.

**Source.** Streams; Steps, One Event, One Step.

**Look for.** The part type and its fields; what is persisted per part;
how a response step is built from its parts, and what a broken stream
leaves.

**Violation.** A step or an event per part; a call path that builds a
whole response first and streams it after; a broken stream that leaves
no step.

**Severity.** medium

**Check.** review

## LIV-03 An input is durable on arrival and delivered at the next call

**Principle.** A person, a program, or another agent may write to a
running session at any time. An input is persisted as a step when it
arrives, so the sender's acknowledgement means it is durable, and a
retried send is one step, by the guideline's edge idempotency. The next
`model_request` delivers every pending input and references it. The
pending inputs are a projection, never a second queue. An input stays
pending until a model request with a complete response has delivered
it, so a crash never drops a steering message.

**Source.** Steering, The Inbox; Loops, Runs, and Sessions, Durable by
Default.

**Look for.** The write path of an input and when it acknowledges; the
idempotency key of a send; how the next request finds and references
the pending inputs.

**Violation.** An acknowledgement before the step commits; a retried
send that writes two steps; an inbox kept in memory or in a queue beside
the steps; an input marked delivered when its request is sent rather
than answered.

**Severity.** medium

**Check.** review

## LIV-04 Waking is decided when an input arrives

**Principle.** Each input is marked waking or not when it arrives, by
the adopter's routing; by default a principal's message wakes and an
external event does not. A non-waking input waits for the next
delivery, and when such inputs pile up they render as a digest. A
message to a parked session waits for the resume, unless it is what the
park waits for, such as the answer to the agent's question.

**Source.** Steering, The Inbox.

**Look for.** Where waking is decided and by whom; how waiting
non-waking inputs render; what a message to a parked session does.

**Violation.** Waking decided at delivery, or fixed in the engine with
no routing; an external event that wakes by default; a pile of
non-waking inputs rendered one by one; a message that resumes a parked
session whose park does not wait on it.

**Severity.** medium

**Check.** review

## LIV-05 Controls never wait in line

**Principle.** Controls travel out of band, never queued behind inputs:
pause parks at the next safe point; resume; cancel ends the loop
`cancelled`, interrupts running tools, and cascades to children;
interrupt stops an interruptible tool; compact; approve and deny; and
unlock clears a park. The engine reads controls between steps and while
a tool runs, and records each as a control step. An urgent message
interrupts an interruptible tool; its response records `interrupted`,
and the model reads the message next.

**Source.** Steering, Controls.

**Look for.** The channel controls arrive on; where the loop reads them,
during a tool's run included; the step each writes.

**Violation.** A control queued with inputs, so a cancel waits behind a
message; controls read only between model calls; a cancel that leaves a
child running; a control applied with no step.

**Severity.** medium

**Check.** review

## LIV-06 Taking over parks the agent

**Principle.** When a person takes over the agent's environment to work
by hand, the agent stands down: its loop parks on a hand-over. When they
give it back, their summary arrives as a message, and an
`environment_changed` step says that a person acted there and what they
did.

**Source.** Steering, Taking Over.

**Look for.** The take-over path and the park it writes; what the
hand-back writes.

**Violation.** A loop that keeps running tools while a person holds the
environment; a hand-back with no `environment_changed` step, so the
model acts on a stale view.

**Severity.** medium

**Check.** review
