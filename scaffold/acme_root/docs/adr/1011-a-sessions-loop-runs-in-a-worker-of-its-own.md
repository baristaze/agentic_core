# ADR 1011: A session's loop runs in a worker of its own

**Status**: accepted (2026-10-02)

## Context

A session's loop is durable work: a person's message arrives through the
API, and the loop runs for minutes after the answer, across model calls
and tool calls. The guideline puts such work in a worker role, never in
the web service (ASY-15), and its work queue carries it as a row with a
lease. A kind that needs only capacity of its own is the maintenance
worker deployed again on a lane of its own.

The loop is not that kind. It holds the model providers' keys, and it
runs what a model asks for in a workspace. The maintenance worker holds
the purge login, the one login that deletes a history (ADR 1010), and
runs every purge. A process that runs a model's tool calls holding that
login puts the one delete the serving logins may not send one convinced
model away from the history.

## Decision

**The loop runs in the session runner, a worker role of its own.** It
claims one kind, `LOOP`, on a lane of its own, and calls the loop's one
operation under the claim's context. A loop holds its claim for minutes,
so the maintenance worker's lane, and its cap on a tenant's share, counts
none of them. It holds no purge login, and the maintenance worker
holds no model key and runs no tool.

**The write that turns a session pending asks for its run.** The
projection that moves a session to `pending` (an input that wakes it, an
unlock, a loop that ends with an input undelivered) lands a `work.LOOP`
outbox row beside the session's write, and the relay enqueues it. Each
turn to pending asks once, and a run that finds nothing to do writes
nothing.

**The lease fences the item; the epoch fences the history.** The runner
renews its lease while a loop runs. A run whose time is up hands its item
back at once, with no attempt spent. A runner that dies leaves its item to
the next sweep of any worker once the lease runs out, and the next run
takes a new epoch, so the lost one can append nothing and send no command.

**The claim loop is the maintenance worker's, taken whole.** The runner
builds the same loop over its one handler, with a sweep that takes back
expired leases and relays the outbox, and purges nothing. A worker that
purges nothing never judges a tenant purged.

## Consequences

- The runner runs on the host locally (`scripts/dev.sh`). It is no
  container in the compose stack and no service in the cloud: a deployed
  runner refuses a workspace on its host, and the cloud's tasks run on
  Fargate, which starts no container from inside a task. The first
  environment that runs a session's loop deploys it, with its image, its
  service, its alarms, and a place its workspaces run.
- A session's loop waits for a runner. With none running, a woken session
  stays pending, and its item waits in the queue, which the queue's age
  alarm reads.
