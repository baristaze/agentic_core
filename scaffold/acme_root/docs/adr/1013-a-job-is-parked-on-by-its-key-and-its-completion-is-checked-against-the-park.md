# ADR 1013: A job is parked on by its key, and its completion is checked against the park

**Status**: accepted (2026-10-03)

## Context

A `job`-mode tool starts work that outlives a run. The spec says the
loop parks on the job, holding no runtime, and that the job's completion
arrives as an event that wakes the session, from which the call's
response is written. It leaves two things open: what the loop keeps of
the job while it waits, and how a completion reaches the session.

Both matter for one reason. The completion is input from outside the
engine. A completion that wakes the wrong session, answers a call twice,
or ends a job the loop never started would let any caller who can write
an event steer a loop.

## Decision

**The park names the job.** A started job parks its loop on `job`, and
the park keeps the job: its key, the id of the call's request; the
tool's own name for the work, its handle; and the budget hold it
started under, when it spends. Its unlock is the key, and it tries again
at the job's deadline. The park lives in the `parked` step and in the
session's cached status, so a run that takes the loop up, a crash
included, reads the job from the history alone, and no table holds it.

**A completion names the key and the handle.** The system the job ran
on reports through one operation, `complete_job`. It is kept only when
the session's open loop parked on a job of that key and that handle,
the call has no response yet, and no completion for it arrived before.
Anything else is `NotFound`, with nothing written: another session's
job, another tenant's, a handle that is not the job's, and a job
answered or reported already. A kept completion lands as an `event`
that references the call's request and holds what the job reported.
When the loop still waits on that job, the `unlock` that clears its park
lands with it; a loop that parked since for another reason reads the
completion once that park clears.

**The run checks it again.** A woken loop trusts nothing that woke it.
The run answers a started job's call from the first event that names
its request and holds that key and that handle, before any model call.
With none, past the job's deadline, it cancels the work and answers the
call as out of time; before it, the loop parks on the job again. A
started job is never gated or started again.

**The response delivers the completion, once.** The event a completion
lands as is the one kind of event that references a step. No model
request delivers it as an input: the call's response carries what it
said, so the model reads it once.

**The loop's end ends the job.** A cancel, and every other way the loop
ends while a job works, cancels the work and answers its call. A tool
that fails to cancel is logged, and the job still ends by its deadline,
which the work was started to keep.

**A job that spends names its rate.** A job tool declares what it costs
an hour at most. The loop holds that rate until the job's deadline
before it starts the work, paid for by whoever the model request that
asked for the call was paid by. A refusal parks the loop on `budget`
with nothing started. The hold settles at the cost the completion
reports, else whole. Only a start refused before any work began
releases it: its input's refusal, or the tool's own `JobRefused`. Any
other failed start may have started the work, and counts it whole.

## Consequences

- A loop parked on a job holds no runtime, no workspace, and no
  connection to the work: only its park.
- A completion is refused before anything is written unless it names
  the job the loop waits on, so a forged or late one wakes nothing.
- A completion that lands while a run holds the loop has no park to
  clear. The run answers from it once it reads it, or, at the latest,
  the run the job's deadline wakes does.
- A run lost between a job's hold and its park leaves that hold open,
  as a run lost between a model call's hold and its request does; a
  recovered run attaches to the work under a hold of its own.
- An adopter's event that references a step is read as a completion and
  never delivered as an input.
