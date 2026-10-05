# ADR 1019: A kind starts sub-agents through a tool, and a report clears its parent's wait

**Status**: accepted (2026-10-05)

## Context

The spec says a sub-agent is spawned through a `spawn`-class tool, and
that a parent keeps working or parks on its children. The agents
manager spawns a child, bounds its tree, and writes a child's report
into its parent (ADR 1018). The spec leaves five things open: what a
spawn tool takes from the model, what a retry of it does, when a parent
waiting on its children wakes, how large a tree is when its kind does
not say, and which policy gates a child whose kind the model chose.

A spawn is a loop that starts other loops, each of which spends. The
model writes its input, and a crash may repeat the call. So the tool
must refuse what the tree does not allow, start no second child on a
retry, never leave a parent waiting on a report that cannot come, and
never let a model choose its way past an approval.

## Decision

**Two engine tools.** `spawn_sub_agent`, of the `spawn` class, starts a
child of the calling session. It takes a title, an objective that
stands on its own, and a kind, which defaults to the caller's. Its
answer names the child's session, its title, and its kind.
`wait_for_sub_agents`, of the `read` class, answers the children whose
loop is open. A kind offers either only by naming it.

**The child's id is the call's.** A spawn runs under the id of the
call's request. A call asked again, after a lost run, answers the child
the first one made, so the tool is safe to repeat. It is not
interruptible: a spawn cut off part way leaves a child that only a
repeat of the same call finishes.

**A bound is a failure the model reads.** The tree's height and count, a
kind that names no share, an unknown kind, and a sender who may not
make a call of the child's registry are each the call's failure, never
a crash. The tree's deadline bounds the call as it bounds every call of
the tree. The spawn does not ask the budget: a child's calls pass the
tree's budget, and a child the budget refuses parks on it, as the spec
says, and the unlock happens at the root.

**A wait parks on children, and only while one runs.** With no child
running, the wait is refused, since every child that ended has
reported. Otherwise the loop parks on `children` once every call of the
turn is answered. An input that landed after the request the turn
answered, a child's report or a person's message, ends the wait before
the park, and the history says so, so a run that takes up a lost one
decides the same.

**The deadline ends a wait.** Past the tree's deadline no report can
come, since every child parks on the deadline too. So the loop reads
the deadline before it parks on its children, and past it parks on the
deadline instead, where its person is asked, as a single agent does. A
child that parks on the deadline wakes a parent waiting on its
children, which then parks on it as well. The run that parks on its
children reads the clock once more after the park, so a child that
parked on the deadline just before finds no park to clear and misses
nothing.

**A wait a report woke is no repeat.** The wait takes no input, so
every wait is the same call, and the error streak ends a loop that
repeats one identical call, since such a loop makes no progress. A wait
that parked and was woken read a report, so its park ends the run of
identical calls, and a parent waits after each report however many
children it has.

**A report clears the park.** When a report that wakes its parent lands
on a parent parked on its children, the agents manager writes the
`unlock` that clears it, and the parent's gates run again as it
resumes. A report can land after the run read its history and before
its park is written, when there is no park for it to clear. The run
that parks reads what came since, and clears its own park when a
report is there. The status projection does not change: a person's
message to a parked parent still waits for the resume.

**A child's calls are gated by every kind above it.** The model names
the child's kind, and a kind's policy is the first layer of the gate.
A child of a looser kind would run unattended a call its parent's kind
holds for a person. So each call of a child is decided under its own
kind's defaults and under those of each kind above it, each with the
tenant's layer and the platform's ceilings, and the strictest decision
holds. A spawn could instead refuse a kind looser than its parent's,
but a rule may key on a target's attributes, which no spawn knows, so
no comparison of two layers before the call is exact; deciding the
call itself under each layer is. An ancestor marked deleted answers no
kind, and takes a layer with no defaults: the tenant's layer decides,
and a call it is silent on waits for a person.

**A tree is three levels deep and holds ten by default.** A kind that
names no tree roots one of height 3, a root, its sub-agents, and
theirs, and a count of 10 sub-agents besides its root.

**The spawn tool reaches the agents manager at call time.** The agents
manager classes every tool of the catalog, the spawn tool among them, so
the tool is built before it and holds a provider of it, as the root
binds its other cyclic edges.

## Consequences

- A model starts a child with one call, and a crash between the spawn
  and its answer starts no second child and no second share.
- A model that reaches a bound reads why and changes its plan, and the
  tree holds no child past its height or its count.
- A parent that waits holds nothing, and wakes on the first report,
  including one that came just before its park.
- A parent that waits is woken by a report, never by a person's message
  alone: a person who must reach it first unlocks it.
- A parent that waits when the tree's deadline passes parks for its
  person, and waits on its children again once the deadline moves.
- A parent waits after each report, however many children it has.
- A kind a spawn names can narrow what its child runs unattended, never
  widen it, and each call of a child costs one more policy decision for
  each level above it.
- A kind that spawns names `spawn_sub_agent` among its tools and a
  policy that allows the `spawn` class; a kind a spawn starts names a
  share.
