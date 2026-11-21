# ADR 1043: The first model call never waits for a workspace

**Status**: accepted (2026-11-20)

## Context

A VM's cold boot takes a minute or more, and a container's start, a
base's restore, or a snapshot's load take seconds. A workspace prepared
before a loop's first model call puts that wait in front of the first
token of every run, including the many runs that never run a command or
touch a file: a question answered from records, a plan, a spawn, a wait
on sub-agents.

A refusal needs no instance. Whether a provider can ever meet a spec is
a fact about the spec and the provider: its mode, its egress, its
limits, whether it can snapshot. Whether a workspace can be made now, on
a free host, a VM that starts, a base that is built, is a fact about the
moment, and only a prepare learns it.

## Decision

**The spec is checked before the first model call; the workspace is
made at the first call that reaches it.** Each provider's `check`
refuses a spec it can never meet, whole, with its cause, and makes
nothing. A refusal there ends the loop `errored` before any call is
spent. A layer's hooks may refuse the session's workspace at the same
point, with its cause, and the run stops as it does on a refused spec
(ADR 1041). The instance is prepared as the first call that reaches the
workspace is about to be gated, its preflight included, so only that
call waits for it.

**A tool says whether its calls reach the workspace.** `ToolSpec.workspace`
is true unless the tool says otherwise, so a tool that says nothing gets
a workspace: a wrong declaration costs a wait, never a refused call. A
call's input may narrow it, as a spawn reaches the workspace only when
it forks. The engine's own tools and every tool over MCP reach none. The
call's request records the answer, and the loop reads it there. A call
that reaches none is given the absent workspace, which every transport
refuses, loudly. It decides nothing of the gate: the rule of two reads
the session's egress, whatever workspace a call was given.

**A workspace that must wait parks the loop with its call open.** A
prepare that refuses with `clears` parks the loop on the resource, and a
lost workspace parks it for a person, at that call. The park is settled
when its own run wrote the call, so the run that takes the loop up runs
it once, under its idempotency key. One written while a lost run's calls
are settled is unsettled, and those calls are settled by their effect. A
refusal that cannot clear ends the loop `errored` there, and each call
the loop left open is answered first, with the refusal's cause, so the
session's next input starts a loop whose history holds every answer.

**A run that reaches no workspace keeps the latest snapshot whole.** It
prepares none, takes no snapshot, and lets nothing go. The next run that
reaches the workspace starts from the latest snapshot, unless a call
that reached the workspace may have run after it, or an
`environment_changed` step came after it. A call that reaches no
workspace touches none, and a call a park the loop wrote held open
never ran. One answered after the snapshot ran then, whatever run
answered it, unless its answer is marked `unstarted`: a refused
workspace answers the calls no run started that way. A job's call ran
in the run that started its job, the run that wrote the first park
naming it, whatever run made its request: a call held for an approval
across a snapshot runs after it. That run parks as soon as the job
starts, so the snapshot it takes at that park holds what the start
wrote, and when none is taken the workspace stands newer than the
latest. The answer the job's end brings touches nothing: the job runs
once the instance is released. A notice the run's own hooks wrote
before its workspace was prepared touches nothing either.

**The hooks are told of the workspace once it is prepared.** `LoopStart`
holds the session, the kind, the epoch, the loop, and whether the loop
is new, and no workspace. Once the run's first call that reaches the
workspace has prepared it, the loop asks the hooks' `workspace_prepared`,
given the run's `LoopStart` and the workspace, for the steps to append,
and writes them before that call is gated: a layer tells the model what
changed in the workspace there, and the model reads it with the call's
answer. A run that reaches no workspace never asks.

## Consequences

- A run's first token waits for no boot, and a run that only reads,
  plans, spawns, or waits makes no instance.
- A spec the provider can never meet is refused before any call is
  spent, and so is a workspace a layer's hooks refuse. What only the moment can say, a host to run on, Docker within
  reach, a base's setup, is learned at the first call that reaches the
  workspace, after model calls were spent, and ends or parks the loop
  there.
- Every workspace provider implements `check`, a layer's own included,
  and every layer's hooks implement `workspace_prepared`.
- A tool that runs a command or touches a file and declares that its
  calls reach no workspace is refused by the transport, loudly.
- A restore a principal or a fork asked for, and a restore lost, is
  applied at the first call that reaches the workspace, and its notice
  reaches the model with that call's answer.
