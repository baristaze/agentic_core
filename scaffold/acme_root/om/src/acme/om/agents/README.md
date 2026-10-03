# Agents

The kinds of agent a product offers, the loop every one of them runs,
and how sessions relate: sub-agents in a tree, and work handed from one
kind to another. This is one of the kinds of thing [Acme is made
of](../../../../README.md).

## What it holds

- **Agent kind**: a profile over the one loop every agent runs: the
  tools it may call, when its work is done, the tool it reports a result
  through, how its calls are allowed, and how large a tree it may grow.
  A product declares its kinds; each is versioned, and a session keeps
  the version it started on.
- **Done rule**: an assistant is done when it answers without calling a
  tool; an agent that delivers work is done only when it submits a
  result.
- **Result gate**: a check a submitted result passes. A claim with no
  evidence is refused. With no gate supplied, a result is accepted and
  marked unverified.
- **Tree**: a session and the sub-agents below it. It has a height (how
  deep it may go), a count (how many sub-agents it may hold), and one
  deadline every session in it shares. Its budget is the top session's.
- **Hand-off**: work one kind passes to another, as a new session of its
  own.
- **Loop**: what an agent does from what woke its session to how the
  loop ended. The model chooses; the loop does. It has no record of its
  own: it is a span of the session's [steps](../steps/README.md), its id
  is its first step's, and a closing step names its outcome.
- **Run**: one stretch of a loop in one process. A run takes the
  session's next writer epoch before it reads anything, so a run that
  lost its claim can write nothing more.
- **Stream part**: a live piece of what a step will hold, numbered and
  naming its step, handed to a carrier for whoever watches and never
  kept.

## What can happen

- **Start** a session on a kind. Its tree starts with it.
- **Spawn** a sub-agent. It starts from a self-contained objective,
  never its parent's history, one level down the tree.
- **Move the deadline** of a tree, for every session in it at once. A
  session that waited on the old one goes on.
- **Cancel.** Cancelling a parent cancels every session below it that is
  not idle, one about to begin its next loop included.
- **Hand off.** The new session holds the objective and where it came
  from, and starts only when its person speaks to it.
- **Submit** a result through the gate.
- **Run** a loop. A run takes up the loop an input woke, or one a park
  let go. It prepares the session's workspace first, renders a request,
  passes the budget gate, writes the request, calls the model, and
  writes the response. Then it runs each tool call the model asked for,
  through policy and the transport, writing each request before it is
  decided and each answer before the next call.
- **End, park, or yield.** A loop ends on the kind's done rule, a bound,
  a principal's cancel, or an error no park can clear. It parks when it
  cannot go on yet, holding no workspace while it waits: on its person,
  when the agent asked them, until a message answers. A run whose time
  is up yields, and the next run goes on.
- **Steer.** A message that lands while the loop runs is delivered by its
  next request. A cancel or a pause cuts in between steps, and a cancel
  or an interrupt stops a running tool.
- **Take over.** A person takes the session's environment to work by
  hand: the running run is fenced, and the loop parks. Giving it back
  tells the model a person worked there, and their account arrives as
  their message.

## The rules

- **A sub-agent holds no more than its parent.** Its tools are cut to
  its parent's, it runs under its parent's person, pays as its parent
  pays, and carries its parent's mark.
- **A tree is bounded.** A spawn past its height or its count is
  refused, and two spawns at once never pass the count.
- **A tree shares one budget and one deadline.** A sub-agent draws on
  what the tree has left; it never gets a budget or a deadline of its
  own.
- **The agent that hands work over cannot steer it.** The objective it
  wrote is data in the new session.
- **Every tree belongs to one org,** and goes when the last of its
  sessions is purged, or with the org's sessions when the org is.
- **Persist before you proceed.** A request is written before its call,
  a response before anything acts on it, a tool's request before it is
  decided, and its answer before the next call.
- **The history says where the loop is.** A run that finds a lost run's
  request with no response closes it and settles its hold in full. A
  tool call a lost run left open is settled, before anything else, by
  what repeating it may do: one that only reads, or is safe to repeat,
  runs again; one that is not is never repeated, and its answer says its
  outcome is unknown. An accepted result is recorded in its answer, and
  the loop ends on it from there.
- **A stale run is refused.** Every step a run writes names its epoch,
  and each tool answer's id is the run's own, so a run that lost its
  claim stops at its next write (ADR 1009).
- **Every model call passes the gate first,** and records who spoke and
  who pays as [attribution](../attribution/README.md) answers.
- **A provider error is handled by its kind.** One worth retrying is
  retried in the process, after a wait that grows and is partly random,
  never sooner than the provider asks. Then the provider is known to be
  failing for that key: every session parks on it until its retry time,
  and this one falls back to its next declared fallback when it has one.
- **A stopped loop never restarts itself.** A loop that ended in an
  error, or that a principal cancelled, starts no new loop on an input
  it left undelivered.
- **A question waits for its answer.** Once the agent asks its person,
  the loop calls no model until a principal's message answers it, and
  the history says so, so a run that takes up a lost one waits too.
- **A nudge is a step.** When a delivery agent's turn calls no tool, the
  engine's notice is written before the next request, so no request
  holds two of the model's turns in a row. A reply cut by its output
  bound is kept truncated, and the model is told so; a request sent again
  unchanged counts toward the error streak. A call that fails the same way
  a few times in a row earns a notice too, before the streak ends the
  loop.
- **An interrupt stops the call it names,** and no other.
- **Isolation is refused, never weakened.** A workspace that cannot meet
  the kind's spec ends the loop before its first model call.
- **Emission never waits.** A part is handed to the carrier, and the
  loop goes on.

## How another namespace composes it

The loop reads and writes through every engine namespace: the session's
status and parks, its steps, its fill set, the window a request reads,
the budget gate, policy and the transport, and attribution. It reads a
session's kind to know when its loop is done and passes a submitted
result through the gate; spawn and hand-off are called from the tools
that offer them. The session runner calls the loop's one operation with
the session and the context its claim built: once each time the session
turns pending, and again when a run's time is up. Nothing else drives a
loop.
Each session is an [agent session](../agent_sessions/README.md); what it
may do and who pays is [attribution](../attribution/README.md)'s.
