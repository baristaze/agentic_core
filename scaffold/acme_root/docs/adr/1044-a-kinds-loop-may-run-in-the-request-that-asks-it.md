# ADR 1044: A kind's loop may run in the request that asks it

**Status**: accepted (2026-11-20)

## Context

ASY-15: "Web services do not spawn background jobs or schedule recurring
tasks; every such need is a worker role with its own container,
deployment, and catalog entry. [...] anything that writes, retries, or
outlives a connection is a worker."

NET-19: "The choice between waiting and not waiting is made per
operation at the client; a long operation returns an acknowledgement
with an id and completes with a push, and the server produces responses
and notifications the same way in both cases."

A session's loop runs in the session runner (ADR 1011): a message is
acknowledged, a work row asks for a run, a runner claims it, and the
answer reaches the client as steps. An assistant that answers from
records needs none of what the runner holds. It touches no workspace,
starts no sub-agent, and waits for no person, and a person waits on its
first token. The queue's hop and the runner's claim stand in front of
that token, and the runner holds nothing the request does not.

## Decision

**Where a loop runs is a kind's own axis.** `AgentKind.runs_in` is
`worker`, the default, or `request`. Every version of a kind runs in
one place, and the loop is the same in both.

**A request kind holds no worker's work.** It has no workspace, no
`spawn`-class tool, no job tool, and no tool its own policy holds for a
person under the platform's ceilings; its step guard is at most 10 and
its run time at most 30 seconds. A root that builds its catalog refuses
a request kind that breaks any of these, every breach named
(`UnsafeConfiguration`). A spawn never starts one, and nobody takes its
environment over: it has none.

**A question runs the loop in its request and streams it.** The API
takes a question that starts a session or continues one the caller
steers, writes it, and runs the loop's one operation in the request.
What the loop emits reaches the request through the process's stream
sink, and the request answers with server-sent events: the session,
each text part with its step id, a ping after 15 seconds of quiet, and
one end with how the run ended, the outcome and its cause, the seqs of
the steps, and the usage. The run takes a writer epoch, passes the one
gate, and is attributed as any run is.

**Nothing waits in a request.** Where another loop would park or yield,
a request kind's loop cancels its jobs, answers each open call as not
run, and ends `inconclusive`, its cause naming the park. Its session's
projection writes no `LOOP` row and no wake row. A caller that goes
away cancels the loop at its next await, and nothing of it outlives the
request.

This is the deviation from both rules: the web service runs and writes
a loop in a request, and the client waits for its answer.

## Consequences

- A request kind's first token waits for no queue and no claim.
- A request holds an admission slot of the write lane for as long as
  its loop runs, at most the kind's run time. The request deadline
  bounds the calls the request makes outside the process, and the
  kind's bounds bound the loop.
- A park becomes the loop's end: a budget, an outage, the step guard,
  or a tenant's policy that holds a call for a person ends the stream
  with its cause, and the next question starts a loop of its own. No
  unlock route has anything to clear.
- A request that dies mid-loop leaves the loop open with its calls in
  flight, and no worker takes it up. The session's next question does,
  under a new epoch, and settles the calls by their effect.
- A question is no create the idempotency record replays: a stream is
  no outcome to record, and a question asked again is a question of its
  own.
- The local and the staging stacks offer an example assistant that
  reads the org's files through one read tool, while the product
  declares no kind of its own.
