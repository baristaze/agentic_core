# ADR 1040: A noun a product could own names the agent or the mechanism it serves

**Status**: accepted (2026-10-14)

## Context

A copy of the scaffold is the product. The engine's nouns and the
product's share one codebase, one database, one API, one command line,
and one tool registry the model reads. A bare session, step, budget, plan, workspace, machine,
model, or artifact is a word a product needs for itself: a clinic's
sessions, a workflow's steps, a campaign's budget, a care plan, a
tenant's workspace, a factory's machines, a trained model, a build's
artifacts. Where the engine and the product meet on one word, one
table, one schema, or one tool name wins, and the product renames its
own.

## Decision

**The engine qualifies the nouns of its agents.** A thing of the agent
says agent: `AgentSession`, `AgentStep`, `AgentPlan`, `AgentWorkspace`.
A thing named for its mechanism says so: a result kept whole outside its
step is a `ToolOutput`, a policy rule a `ToolPolicyRule`, a budget a
`SpendBudget` with its `SpendHold`, `SpendSettlement`, `SpendBreach`, and
`SpendTally`, the model roles live in `language_models`, and the
machines a VM workspace runs on are `vms`. The bare word stays the
product's.

**On every surface a copy shares.** The qualified name is the
namespace, the class, the table with its columns and indexes, the wire
type and the route, the command line's group, the permission, the work,
outbox, and event kinds, the stored values, the settings and their
variables, the bucket, the metric label, the model-visible tool, and the
skill. The scopes a budget caps name an agent session and an agent
project (`agent_session`, `agent_project`).

**The prose keeps the short word.** The spec, the lenses, and the
READMEs say "a session" and "a step", and the spec names the code where
it defines a noun ("In code it is `AgentStep`"). A mechanism word, such
as cursor, park, or fence, and a word path-qualified under
`/agent-sessions`, such as its steps or its controls, stay as they are.
Where a noun leaves the vocabulary, the prose follows it: a kept result
is a tool output, and a VM workspace runs on VMs.

**A rename is one breaking release.** Tables, columns, and indexes move
by migrations that rename them, never drop and create them, and the
stored values move in place in the same migrations, each with a
downgrade that restores both. No alias, view, or shim keeps an old name.
A route renamed inside `/v1` departs from the guideline's rule that a
removal takes a new prefix, since no deployed copy serves a customer.

**The role check knows a renamed table by its old name.** Each table
the engine renamed is listed by the name it had in
`DROPPED_TABLE_ROLES`, below its marker, so a copy's chain that still
names it passes the role check.

## Consequences

- A product's own session, step, budget, plan, workspace, machine,
  model, or artifact takes the bare word on every surface, and needs no
  rename of its own.
- A new engine noun is held to this rule before it lands: a word a
  product could own names the agent or the mechanism it serves.
