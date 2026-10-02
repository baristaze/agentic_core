# Trust

Group id: `trust`. Covers Identity, Trust, and Attribution of
`agentic_core_spec.md`.

This group judges who stands behind each step: the context an operation
takes, the actor, the principal, and the spender, the authority a tool
call runs under, which content instructs and which is data, how the
engine bounds a convinced model, and the trace. It leaves policy and
approvals to `tools`, the pinned zone to `windows`, a child's registry
and principal to `agents`, and the gate that spends nothing when no one
pays to `bounds`.

## TRU-01 The engine never mints a context

**Principle.** The engine's operations take the guideline's context
first: a tenant operation takes `TenantContext`, and a consumer that
reads less declares a scope. The engine never mints a context; a
transition does, in the adopter. The plumbing (claiming, appending,
settling, recovering) runs under the context the guideline's claim
builds: the principal who woke the loop, under the service role, or the
tenant's service context when none is live.

**Source.** The Engine and the Brain; Identity, Trust, and Attribution,
Who Is Who.

**Look for.** The signatures of the engine's operations; every place a
context is built inside the engine; the context the plumbing runs
under.

**Violation.** An engine operation that takes no context, or takes it
after its other arguments; a context constructed inside the engine;
plumbing that runs under a context of its own making.

**Severity.** medium

**Check.** review

## TRU-02 Actor, principal, and spender are three answers

**Principle.** Every step names its actor: a person, a program, an
agent, the model, the engine, or something external. Inputs and tool
calls name their principal, on whose authority they run. Model requests
name their spender, who pays for the call. Each answer is its own field,
attached where the spec attaches it.

**Source.** Identity, Trust, and Attribution, Who Is Who.

**Look for.** The actor on every step, the principal on inputs and tool
calls, and the spender on model requests.

**Violation.** A step with no actor; a tool call with no principal; a
model request with no spender; one field standing for two of the
three.

**Severity.** medium

**Check.** review

## TRU-03 The agent acts and holds no authority

**Principle.** The agent is an actor, never a principal: it acts, and it
never holds authority of its own. Its steps name it as their actor, with
its kind and session in the header, so an audit answers "which agent"
without the agent owning a permission.

**Source.** Identity, Trust, and Attribution, Who Is Who.

**Look for.** Whether an agent appears as a principal or holds a grant;
how an audit finds the agent behind a step.

**Violation.** The agent's own identity holding a permission or a
grant; a tool call authorized on the agent's identity instead of a
principal's; an agent's output that counts as a principal's input. (A
service principal the tenant grants a steady kind is TRU-04.)

**Severity.** high

**Check.** review

## TRU-04 Each agent kind picks an authority mode

**Principle.** Each agent kind picks its authority mode for tool calls.
Delegated: tools run with the asking person's live permissions,
re-checked on every call by a transition of the adopter's tenancy
manager, never the system's, and the engine records the re-check as a
decision. Steady: tools run under one principal fixed at the session's
creation, its creator or a service principal the tenant grants; when
that principal is no longer valid, its tool calls park until a person
assigns another.

**Source.** Identity, Trust, and Attribution, Who Is Who.

**Look for.** The kind's mode; for a delegated kind, the re-check on
each call and the decision it records; for a steady kind, the fixed
principal and what happens when it lapses.

**Violation.** A delegated call checked once per session or per loop; a
re-check run under the system's context; a steady session that keeps
running tools under a principal that lost its access.

**Severity.** medium

**Check.** review

## TRU-05 Only a principal instructs

**Principle.** Content has two trust tiers, and the renderer keeps them
apart. Instructions are the agent kind's prompts and the engine's
notices; a principal's messages through a product surface; and, for a
child, its parent's objective and messages, under the authority its
spawn was granted. Everything else is data: tool output, external
events, documents, attachments, recalled knowledge, third-party tool
descriptions, summaries of the window, and a child's report to its
parent. Data renders in the provider's native result blocks, quoted,
with delimiters escaped, labelled with its origin, and explicitly not an
instruction. Text never grants power.

**Source.** Identity, Trust, and Attribution, Only a Principal
Instructs; Context, Rendering.

**Look for.** How the renderer places each kind of content; the quoting
and the origin label on data; any path where content changes what may
run.

**Violation.** A tool result, an event, or a child's report rendered as
an instruction; data with unescaped delimiters or no origin label;
content that raises a permission, extends an approval, or sets a tool's
class.

**Severity.** high

**Check.** review

## TRU-06 A marked session acting outward needs a person

**Principle.** The engine assumes the model can be convinced of
anything and bounds what a convinced model can do: the registry, policy
and approvals, egress, and budgets. A session carries an untrusted mark
from the first data it reads; the mark is sticky and passes to its
children and handoffs. A session that is marked, holds private data or
credentials, and can act outward needs a person to approve its outward
calls. Outward means external state beyond the session's own work
product, its own branch and a pull request on the tenant's bound
repository, and any egress beyond its allowlist.

**Source.** Identity, Trust, and Attribution, Bound What a Convinced
Model Can Do.

**Look for.** Where the mark is set, and whether anything clears it; how
a child and a handoff take it; the policy rule that combines the three
conditions; what counts as outward.

**Violation.** A mark set on some data only, or one that is cleared; a
child or a handoff that starts unmarked; a marked session holding
credentials that pushes to another branch, posts outside, or reaches
past its allowlist unattended.

**Severity.** high

**Check.** review

## TRU-07 The person who asked pays

**Principle.** The spender of a model call is the principal behind the
latest principal-authored input the model received. An input from an
agent, the engine, or an external event never becomes the payer; the
current spender carries over. A child inherits its spender from its
spawn, and an automation's trigger is paid by the automation's
principal.

**Source.** Identity, Trust, and Attribution, The Person Who Asked Pays.

**Look for.** Where a model request's spender is set, and what it reads.

**Violation.** A spender taken from the latest input whoever wrote it;
an external event or an agent's message that becomes the payer; a child
that pays as anyone but its spawn's spender. (A call made when no one
can be told to pay is BND-02.)

**Severity.** medium

**Check.** review

## TRU-08 A trace is a query, and spans carry shape

**Principle.** Tracing follows the guideline's correlation across a
handoff, and an input that joins a running loop is a link, never a
parent. The `model_request` that delivered an input references it, so
the trace of any tracking id is a query, and no step carries a growing
list. A sub-agent's loop links to the tool call that spawned it. Spans
follow a pinned version of the OpenTelemetry GenAI conventions, with
content capture off: they carry shape, never content.

**Source.** Identity, Trust, and Attribution, Tracing.

**Look for.** How a joining input is linked; what a step carries for
tracing; the span conventions and their version; the content-capture
setting.

**Violation.** A joining input made the parent of the loop's span; a
step with a growing list of tracking ids; a conventions version left
unpinned; content captured in a span.

**Severity.** medium

**Check.** review
