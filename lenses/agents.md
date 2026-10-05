# Agents

Group id: `agents`. Covers Agent Kinds and Sub-Agents of
`agentic_core_spec.md`.

This group judges the profiles over the one loop and how sessions
relate: agent kinds, done rules and the result gate, sub-agents and
their tree, and handoffs. It leaves the class and policy of the spawn
tool to `tools`, the untrusted mark a child inherits to `trust`, the
gate each call of the tree passes to `bounds`, and the null result
gate's marked verdict to `privacy`.

## AGT-01 An agent kind is a versioned profile over one loop

**Principle.** One loop serves many agents. An agent kind is a profile
over it: prompts, a tool registry, model roles, a done rule, a result
contract and gate, an authority mode, and default bounds. A kind is
versioned. A session pins its kind version as it pins its fill set; an
upgrade happens at a loop boundary and is recorded as a `switched` step.

**Source.** Agent Kinds and Sub-Agents, Agent Kinds.

**Look for.** How a kind is declared and versioned; branches on a kind
inside the loop; where a session's kind version changes.

**Violation.** A second loop for one kind; loop code that branches on a
kind's name; a kind upgraded mid-loop, or with no `switched` step.

**Severity.** medium

**Shape.** `scaffold/acme_root/om/src/acme/om/agents/types/kind.py`

**Check.** review

## AGT-02 A done rule ends a loop, and a result gate checks the claim

**Principle.** When a loop is done depends on the kind. For an
assistant, a turn with no tool call is the answer. For an agent that
delivers work, only its result tool ends the loop; a turn without a tool
call earns a nudge to continue or submit, and repeated nudges end the
loop `inconclusive`. The result tool passes a result gate, an injected
check that can refuse: a claim of success with no evidence behind it is
refused, and a failure explained with evidence is accepted as `failed`.
The gate's null object accepts and marks the result unverified.

**Source.** Agent Kinds and Sub-Agents, Done Rules and the Result Gate.

**Look for.** Each kind's done rule; the nudge and its count; the result
gate's injection and its null object.

**Violation.** A delivery kind whose loop ends on a turn with no tool
call; a success accepted with no evidence; a null gate that passes a
result as checked.

**Severity.** medium

**Shape.** `scaffold/acme_root/om/src/acme/om/agents/rules.py` and
`scaffold/acme_root/om/src/acme/om/agents/impl/gate.py`

**Check.** review

## AGT-03 A sub-agent is a session with a parent and a clean context

**Principle.** A sub-agent is a session with a parent, spawned through a
`spawn`-class tool, so it is gated and audited like any other power. It
starts from a self-contained objective, the constraints that bind it,
its bounds, and the shape of a good report, and never inherits its
parent's history. Its report reaches the parent's inbox, as data, when
its loop ends or when it needs a person; the parent learns of each
status change when it happens and never polls. A parent keeps working,
or parks on its children. Cancellation cascades from parent to children.

**Source.** Agent Kinds and Sub-Agents, Sub-Agents.

**Look for.** The spawn path and its tool's class; what a child's first
request holds; how a child's report and status reach the parent; the
cancel path.

**Violation.** A child started outside a `spawn`-class tool; a child
seeded with its parent's history; a parent that polls its children; a
cancel that leaves a child running.

**Severity.** medium

**Shape.** `scaffold/acme_root/om/src/acme/om/agents/impl/manager.py`

**Check.** review

## AGT-04 A child never escalates

**Principle.** A child's registry and principal are at most its
parent's: a spawn gives the child no tool its parent's registry lacks,
and no authority its parent's principal lacks. Each of its calls is
decided under its own kind's policy and under that of every kind above
it, and the strictest decision holds.

**Source.** Agent Kinds and Sub-Agents, Sub-Agents (No escalation).

**Look for.** How a child's registry and principal are chosen at spawn,
and the check against the parent's; the policy layers the gate decides
a child's call under.

**Violation.** A child given a tool its parent lacks, or run under a
principal with more authority than its parent's; a child's registry
taken from its kind with no limit by the parent's; a child's call
decided under its own kind's policy alone. (A child that starts
without its parent's untrusted mark is TRU-06.)

**Severity.** high

**Shape.** `scaffold/acme_root/om/src/acme/om/agent_sessions/rules.py` and
`scaffold/acme_root/om/src/acme/om/attribution/rules.py`

**Check.** review

## AGT-05 The tree is bounded in height, count, and time

**Principle.** The tree is bounded. Height 1 is a single agent, and 2
lets the root have children that have none. Count is the most
sub-agents the tree may hold besides its root, and concurrency, when it
is set, the most that run at once. Time is one absolute deadline the
whole tree shares: one instant, never a duration per call. A child
parked on a budget or a provider does not disturb its parent: the wait
belongs to the tree, and the unlock happens at the root.

**Source.** Agent Kinds and Sub-Agents, Sub-Agents.

**Look for.** Where height, count, and concurrency are checked at spawn;
how a child gets its deadline; where a child's budget or provider park
is unlocked.

**Violation.** A spawn that skips the height or the count check; a child
given a fresh duration instead of the tree's instant; a child's budget
park unlocked on the child, or one that parks its parent.

**Severity.** medium

**Shape.** `scaffold/acme_root/om/src/acme/om/agents/rules.py` and
`scaffold/acme_root/om/src/acme/om/agents/types/tree.py`

**Check.** review

## AGT-06 The root's budget bounds the whole tree

**Principle.** The root's budget bounds the whole tree. A child draws on
what the tree has left, never a copy of its parent's budget, and never
creates budget.

**Source.** Agent Kinds and Sub-Agents, Sub-Agents.

**Look for.** How a child's budget is set at spawn; the scopes its model
calls are gated against.

**Violation.** A child given a budget in place of the tree's, such as a
copy of its parent's or budget created outside the root's; a child's
calls gated on a scope the root's budget does not bound.

**Severity.** high

**Shape.** `scaffold/acme_root/om/src/acme/om/agents/types/tree.py` and
`scaffold/acme_root/om/src/acme/om/agents/types/request.py`

**Check.** review

## AGT-07 A handoff's principal confirms its objective

**Principle.** Handing work from one kind to another uses the same
primitive as a sub-agent. The new session carries a self-contained
objective and its origin, and its principal confirms the objective
before it starts. The agent that started it steps back and cannot steer
it beyond that objective.

**Source.** Agent Kinds and Sub-Agents, Handoffs.

**Look for.** The handoff tool and what it creates; the confirmation
before the new session's first loop; what the starting agent can still
send to it.

**Violation.** A handoff session that starts on the agent's objective
with no principal's confirmation; a starting agent whose messages can
change the new session's objective.

**Severity.** high

**Shape.** `scaffold/acme_root/om/src/acme/om/agents/impl/manager.py`

**Check.** review
