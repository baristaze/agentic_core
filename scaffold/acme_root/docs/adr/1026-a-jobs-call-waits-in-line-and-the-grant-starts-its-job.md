# ADR 1026: A job's call waits in line, and the grant starts its job

**Status**: accepted (2026-10-09)

## Context

A tool whose work runs long on a scarce resource is a job tool: its work
outlives a run, so the loop parks on the job and its completion answers
the call (ADR 1013). It also waits in line for the resource first. A
session's ask in line is a quick tool's: it answers at once with its
place, the loop parks when the turn ends, and the grant reaches the
model as the engine's notice (ADR 1024). Neither fits the two together.
A job tool that answered with its place would need a model call to
start its work once granted. One that parked on its job would wait on a
job nothing has started, and no grant would reach it.

The guideline's lease can start a job: the grant writes the job's work
item in its own commit, and the worker that claims the item starts,
renews, and ends the lease (ADR 0094).

## Decision

**A job's tool names the request its work waits on.** Its run asks in
line as ADR 1024's tools do, as the session's waiter, under the call's
key, and answers `JobStarted` naming the request. The resource's kind
starts the work in the grant's commit.

**The call parks in line at once.** The call stays open: nothing can
answer it until the job runs, so the loop parks without waiting for the
turn's end. The park is on `resource`. It names the request, its place
and estimate, and the job, and tries again at the job's deadline. The
grant's notice unlocks it as it unlocks any park in line, and the run
reads the request again once its park lands, so a grant just before the
park is never missed.

**The grant moves the park to the job's.** A run that finds the request
granted parks on the job, with no model call and no notice. The job's
completion answers the call as any job's does. A request that left its
line without a lease answers the call with its reason: no job started,
so its hold is released. Past the job's deadline in line, the job is
cancelled and the call says it never started. The job's park keeps the
request past the grant, beside the job's key, handle, and hold (ADR
1013), so every cancel of the job names it, in line or granted.

**A loop that stops leaves its lines before it cancels its jobs.** A
cancel, or any other end, with a job in line leaves every line first, so
no grant starts a job for a loop that stops.

## Consequences

- A job that waits for a resource costs no model call between its ask
  and its result, and the model reads its result, never its grant.
- A session cancelled while its job waits in line leaves the line, and
  no job starts.
- The tool's cancel ends its job's ask, or its lease once granted, as it
  ends any job's work.
- A park in line carries a retry time when it waits for a job's start:
  the job's deadline.
