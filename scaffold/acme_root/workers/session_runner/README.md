# The session runner

The process that runs the loops of agent sessions. A person's message, an
unlock, or a loop that ends with a message still waiting turns a session
pending, and the write that does it asks for a `LOOP` work item. The runner
claims it and calls the loop's one operation,
[`LoopManagerInterface.run`](../../om/src/acme/om/agents/loop.py), under the
context the claim built: the person who woke the session, on the service
role. Every tool call asks that person's own permissions again
([ADR 1007](../../docs/adr/1007-a-delegated-call-asks-the-persons-permissions-again.md)).

It is a worker role of its own, beside the maintenance worker, and never
inside the API ([ADR 1011](../../docs/adr/1011-a-sessions-loop-runs-in-a-worker-of-its-own.md)).

## How a loop runs here

- **The claim** takes the item under a lease, and the runner renews it
  while the loop runs. It is the maintenance worker's claim loop, taken
  whole: capacity, the renewal and its fence, liveness, and the drain on
  stop.
- **The lane** is the loops' own, `loop` (`ACME_RUNNER_LANE`): the relay
  lands every `LOOP` item there, so a cap on the maintenance worker's lane
  counts none of a tenant's running loops, and its wakes still run. A
  runner set to a lane the relay lands no loop on refuses to start, and
  names both.
- **The run** takes the session's next writer epoch before it reads the
  history. A run that lost its claim, to a lease that ran out or to a
  person who took the environment over, can append no step and send no
  command.
- **A run whose time is up** hands its item back at once, with no attempt
  spent, and the next claim goes on with the loop. A run that finds its
  session gone completes the item. One that misses anything else, such as
  a kind this runner does not declare, fails it: it is retried, and past
  its attempts dead-lettered, where an operator requeues it. Any other end
  completes the item.
- **A runner that dies** leaves its item claimed until the lease runs out.
  The next sweep, of any worker, puts it back, and the next run settles
  the calls the lost one left open by their effect: one that only reads or
  is safe to repeat runs again, and one that is not is answered as
  interrupted, never repeated.
- **The sweep** takes back the expired leases and relays the outbox a crash
  left. It purges nothing: the purges, and the purge login, are the
  maintenance worker's.

The runner holds the model providers' keys and runs tools in the
workspaces its settings name. It holds no purge login.

## What a product gives it

The agent kinds and the tools are the product's. A product's own entry
point passes them to `main`, as `tests/e2e_runner.py` passes the suite's,
and its API passes the same kinds, since a session starts on one of them.
Its settings carry the prefix `ACME_RUNNER_` for its own knobs, and read the
rest as every process does (`.env.example`).

`serve` runs the process; `health` asks the running process's `/healthz`.

## Test

```bash
uv run pytest -q workers/session_runner/tests -m "not integration"
make migrate && uv run pytest -q workers/session_runner/tests -m "integration and not live"
make test-live    # one loop on a live provider; spends, within a budget the case sets
```

The end-to-end suite runs the API in the test's process over the compose
stack and the runner as a process of its own on the scripted model. It
kills a runner with a signal in the middle of a tool call and shows the
next one take the loop over.
