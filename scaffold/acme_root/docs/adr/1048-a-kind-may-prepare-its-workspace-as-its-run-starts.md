# ADR 1048: A kind may prepare its workspace as its run starts, beside its first model call

**Status**: accepted (2026-11-24)

## Context

ADR 1043 makes a run's instance at its first call that reaches the
workspace. No model call waits for a boot, and a run that reaches no
workspace makes none. The price is paid by that first call: it waits
for the whole prepare, after every model turn before it. A prepare that
boots a machine takes tens of seconds.

For many kinds that price buys little, since nearly every run reaches
the workspace. `performance/shapes/long-session.json` holds the shape of
one long session of a coding agent. 182 of its 195 runs reach the
workspace. The model time before a run's first call that reaches it is
3.3 s at the median and 19.4 s at p95. A prepare that starts with the
run would be that far along when the call comes.

A prepare that starts with the run has a cost too. A run that reaches
no workspace pays for an instance no call uses. Only the kind knows how
rare that run is.

## Decision

**A kind says whether its runs prepare early.** `AgentKind.prepares_early`
is False unless the kind says so, and only a kind that works in a
workspace may say so. A kind that declares nothing runs as ADR 1043
says.

**The prepare starts beside the run's first model call.** It starts
after the hooks have named the steps before the first model call, since
they may refuse the workspace there, and only when the run holds no
instance (`LoopWorkspace._prepare_early`). It starts from what the
history names then, as a first call's prepare would. It is the
provider's prepare alone, run as a task: every step about the
workspace, a restore's notice and the hooks' `workspace_prepared`, is
still written on the first call's path.

**The first call that reaches the workspace takes it.** The call waits
for the prepare while it runs, and uses what it made (`_taken`). When
the prepare failed, or started from another step than the call would,
the call prepares as any first call does. A provider finds what the
early prepare started, and a refusal that stands reaches the call as it
would have. One prepare serves the run.

**A run that reaches no workspace lets the instance go.** Its end waits
for the prepare, so no prepare outlives its run, and lets the instance
go through the release any run's end makes (`_let_go_early`). It takes
no snapshot, since no call touched the workspace, and counts the
instance in `acme_workspace_prepares_unused_total`, by kind. A run whose
own task is cancelled cancels the prepare instead. A run that lost its
claim waits for it and lets nothing go: what it made is the claim
holder's.

**A workspace that may hold what no snapshot holds is never prepared
early.** A workspace kept by snapshots is prepared early only when a
snapshot holds it or nothing has touched it (`rules.untouched`). One
touched since its latest snapshot may still stand live, and its first
call finds it as it stands, as ADR 1043 says. So no end lets go an
instance that a call changed.

## Consequences

- The first call of a kind that prepares early waits only for what is
  left of the prepare. On the shape, that is up to 3.3 s less at the
  median and 19.4 s less at p95.
- A run of such a kind that reaches no workspace pays for an instance
  until its end. The counter, read against the kind's runs, says how
  often. A kind whose runs often reach no workspace declares nothing.
- A run whose prepare is still going at its end ends when the prepare
  does, then lets the instance go.
- A provider and a layer's hooks need nothing new. The prepare is the
  provider's own, and `workspace_prepared` is still asked at the first
  call that reaches the workspace.
