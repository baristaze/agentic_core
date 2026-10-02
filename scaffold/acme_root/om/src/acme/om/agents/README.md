# Agents

The kinds of agent a product offers, and how sessions relate: sub-agents
in a tree, and work handed from one kind to another. This is one of the
kinds of thing [Acme is made of](../../../../README.md).

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

## What can happen

- **Start** a session on a kind. Its tree starts with it.
- **Spawn** a sub-agent. It starts from a self-contained objective,
  never its parent's history, one level down the tree.
- **Move the deadline** of a tree, for every session in it at once.
- **Cancel.** Cancelling a parent cancels every session below it that is
  still working.
- **Hand off.** The new session holds the objective and where it came
  from, and starts only when its person speaks to it.
- **Submit** a result through the gate.

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
- **Every tree belongs to one org,** and goes with the org's sessions
  when the org is purged.

## How another namespace composes it

The agent's loop reads a session's kind to know when its loop is done,
passes a submitted result through the gate, and calls spawn and
hand-off from the tools that offer them. Each session is an [agent
session](../agent_sessions/README.md); what it may do and who pays is
[attribution](../attribution/README.md)'s.
