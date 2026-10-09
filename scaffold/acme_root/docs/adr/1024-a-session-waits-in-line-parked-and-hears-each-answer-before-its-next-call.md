# ADR 1024: A session waits in line parked, and hears each answer before its next call

**Status**: accepted (2026-10-08)

## Context

The scaffold's leases namespace gives a scarce resource a lease under a
fencing token, and a line in front of it (ADR 0086). A waiter registers
its hooks: whether it still waits, and the rows a grant, an end without
a lease, and a revocation land to tell it. Its own waiter is a
long-running record parked on `resource`.

An agent session is the other thing that waits. A tool that needs a
resource asks for it on the session's behalf. The model must not spend
calls while the resource is held by someone else: asking "is it mine
yet?" each turn is polling, and a park nothing wakes is a session that
sleeps forever. A grant can also land between the moment a run reads its
ask waiting and the moment its park lands, and then there is no park for
it to clear.

## Decision

**A session is a waiter kind.** The engine registers `session` beside
the orchestration. A session still waits while its loop is open, that
is, while it is not idle. A grant, an end without a lease, and a
revocation each land a `LEASE_NOTICE` work item for the session in the
same commit.

**A tool asks, and answers at once.** A tool whose output is `InLine`, or
built on it, asks in line. Its request's id and key are the call's key,
so a call repeated after a lost run answers the request the first one
made and joins no line twice. It answers with the request, and its lease
when granted at once, or its place and estimate. The loop reads its asks
from those answers.

**The loop parks only when the turn ends.** When the model ends its turn
with no call while an ask waits, the loop parks on `resource`, unlocked
by `grant`, with no retry time. The park names the request nearest its
grant, its place, and its estimate. The request's own wait bounds the
park: past it the sweep ends the request, which wakes the session. A
loop woken while every ask still waits parks again with no model call.
Something the model has not read, a waking input or a notice, gets a
model turn instead, and the turn is not judged while an ask waits, so
the loop never ends with a request in line.

**The handler only unlocks; the run reads and tells.** The `LEASE_NOTICE`
handler unlocks a session parked in line and leaves any other as it is.
Before each model call, a run reads the session's asks, across its
loops, and tells the model each answer it has not been told, as the
engine's notice: the grant with its lease and token, while the lease is
active; the request's end with its reason; the lease's end, released,
expired, or revoked. A lease outlives the loop that took it, so its end
is told in a later loop too. Once a lease has ended, its end is told in
place of the grant, so the model never acts under a dead token. Each
notice's id is derived from the call's answer and what
the notice tells, so the history says what was told, after a lost run as
well. A grant the call answered with needs no notice. The engine writes
the notice under the run's epoch, so it instructs and never arrives
through the inbox. An ask whose end was told is read no more.

**The run reads again after it parks.** Once its park lands, the run
reads its asks again, and clears its own park when one has an answer
the model was not told. A grant before that read is found by it; one
after it finds the park and clears it through the handler.

**A loop that ends leaves every line.** The loop's end leaves the lines
of the session as a waiter, before its `loop_ended` step, so a cancel,
an error, or an answer never leaves a place held. An archive or a delete
needs an idle session, whose loop has ended already.

**Past the deadline, no line and no lease.** A turn that ends past the
tree's deadline parks on the deadline, as a wait on children does, never
in line. A loop that parks on the deadline leaves every line, so no
grant holds a resource for a session out of time. It also releases each
lease its asks hold, as their holder: the principal the session's calls
run under, asked of attribution as a call's is. The model is told each
end once. A principal that no longer holds the session's calls releases
nothing, and its lease runs to its term.

## Consequences

- A session in line costs no model call between its park and its
  answer.
- A grant that lands while the run is parking still wakes it.
- A session cancelled in line leaves it, and the next request is
  granted.
- A session that asked in line reads its asks once before each model
  call, across its loops, until each request's or lease's end is told.
- A revocation after the loop that took the lease has ended reaches the
  model in the next loop's first call.
- A session out of time holds no place in a line, and no lease.
- The API's park view names the line: the request, its place, and its
  estimate.
