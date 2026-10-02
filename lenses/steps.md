# Steps

Group id: `steps`. Covers The Engine and the Brain; Steps; Loops, Runs,
and Sessions; and History of `agentic_core_spec.md`.

This group judges the record and the loop that writes it: who decides
what, what a step is, how steps make loops and sessions, how the engine
persists before it acts and recovers after a crash, and where each part
lives. It leaves the context a model reads to `windows`, the tool
contract and the effect a tool declares to `tools`, stream parts and the
inbox to `live`, the context an operation takes to `trust`, the guards
that park a loop to `bounds`, and sealing a step's content to `privacy`.

## STP-01 The loop is the engine's; the model only chooses

**Principle.** The model chooses; the engine does. The loop is written
in the engine, never borrowed from a framework that decides durability,
bounds, or approvals on its behalf. When the model asks for a tool, the
engine checks the policy, runs the tool, records the call, meters it,
and hands back the result. Durability, bounds, approvals, attribution,
and the record are engine semantics, never model behavior.

**Source.** The Engine and the Brain.

**Look for.** The loop's code and what it imports; where the decisions
to persist, to park, to ask for an approval, and to end a loop are made;
every path from a model's tool use to a tool's run.

**Violation.** A loop driven by an agent framework that persists,
retries, or approves on its own; a bound held only by an instruction in
a prompt; a tool run with no step recorded for it. (An approval the
model gives is TOL-07.)

**Severity.** medium

**Check.** review

## STP-02 The engine is pure, and everything it touches is injected

**Principle.** The engine is pure. Everything it touches is an injected
interface: step and session storage, blob storage, the key service, the
budget ledger, pricing, model providers, the workspace provider and the
transport, the outage signal, the clock, and the stream sink. A laptop,
a container, a VM in a customer's cloud, and a unit test are all valid
hosts.

**Source.** The Engine and the Brain.

**Look for.** The engine's imports and constructors; any database
driver, provider SDK, cloud client, or wall clock the engine reaches
without an interface; how a test builds the engine.

**Violation.** A database or provider client imported by the loop;
`datetime.now()` or a sleep on the wall clock in engine code, so a test
cannot fake time; an engine that cannot be built in a unit test with
nothing on the network.

**Severity.** medium

**Check.** review

## STP-03 A request and its response are two steps

**Principle.** A request and its response are two steps, never one
turn. A response may arrive much later, in parts, or never, so a request
never waits for its response to exist, and a response points at its
request with `responds_to`. No step is two things, and none copies
another: a `tool_request` references the tool-use block of the response
that asked for it and carries its input's hash, and a `model_request`
references the steps it carried.

**Source.** Steps, One Event, One Step.

**Look for.** The step types and the code that writes each request and
response; `responds_to` on every response; what a `tool_request` and a
`model_request` hold of the steps they come from.

**Violation.** A turn record that holds a call and its answer together;
a response with no `responds_to`; a `tool_request` that copies the
tool-use block instead of referencing it and carrying its input's hash.
(A request written only once its response arrives is STP-10.)

**Severity.** medium

**Check.** review

## STP-04 The type answers questions, and content is one block model

**Principle.** A step's type answers questions, `is_tool_call()`,
`is_model_call()`, `is_input()`, and `is_summary()`, so no caller
compares strings. Content is one provider-neutral block model: text,
image, document, thinking, tool use, tool result. Each step type offers
a typed view over it, such as `as_text()` or `as_tool_response()`, and
there is no second content type. The engine emits domain types, never
raw tokens, provider payloads, or loose dictionaries.

**Source.** Steps, Step Types; What a Step Carries.

**Look for.** Callers that branch on a step's type; the content types and
the views over them; what the engine returns to its callers and emits to
its sink.

**Violation.** A caller comparing a step's type to a string; a content
class per provider, or a dictionary standing for a block; a provider's
response object or raw token text passed out of the engine.

**Severity.** medium

**Check.** review

## STP-05 The sequence is gapless, and ids are minted above storage

**Principle.** Every step carries a `seq`, gapless per session, which
one atomic append takes from a cursor row per session, so a gap reads as
a loss. It exists because the model reads order and clocks lie. Ids are
`uuid_v7`, minted above storage. A record a second run must find, such
as a tool's execution, takes `derived_id` from its request step.

**Source.** Steps, What a Step Carries.

**Look for.** The append in each step storage impl and where it takes
the sequence; what orders a read of the history; where step ids and the
ids of execution records are minted.

**Violation.** A sequence from a database sequence or a counter outside
the append, so a failed append leaves a gap; a history ordered by a
timestamp; a step id assigned by storage; an execution record given a
fresh id, so a second run cannot find it (the call it then repeats is
STP-11).

**Severity.** medium

**Check.** review

## STP-06 A step holds placeholders; the bytes live in blob storage

**Principle.** Children belong to a step without being its main
content: the model's thinking, attachments, and the usage of a model
call. An attachment sits in the step as a placeholder (id, name, media
type, size, keyed hash), never as bytes. The bytes live in blob storage,
sealed like the step, and render into a request deterministically,
within the model's limits, so the prompt stays stable and its cache
stays warm.

**Source.** Steps, What a Step Carries.

**Look for.** How an attachment is stored on a step; where its bytes go
and how they are sealed; how a request renders it.

**Violation.** Attachment bytes inline in a step's content; a blob
stored unsealed beside a sealed step; a render that fetches or resizes
an attachment differently on each call, so the same steps render
different bytes.

**Severity.** medium

**Check.** review

## STP-07 A step is written once, and every grouping references it

**Principle.** A step is written once and never rewritten. Every
grouping in the engine (a loop, a window, a snapshot, a tree, a trace)
holds references to steps, never copies: content is sealed, and every
copy is one more place to protect and destroy.

**Source.** Steps, Written Once, Referenced Everywhere.

**Look for.** Update and delete paths in step storage; the types of
loops, windows, snapshots, trees, and traces, and what each holds.

**Violation.** An update of a step after its append; a window, snapshot,
or trace that holds step content; a tree or loop record that duplicates
steps.

**Severity.** medium

**Check.** review

## STP-08 Five outcomes end a loop, and a park is none of them

**Principle.** A loop is a span of steps: `loop_id` is its first step's
id, a `loop_ended` step closes it, and it has no table. It ends in one
of five outcomes: `succeeded`, when the kind's done rule was met and its
result gate accepted the result; `failed`, a conclusion with evidence
that the objective cannot be met as asked; `inconclusive`, a stop
without a conclusion; `cancelled`, by a principal; or `errored`, an
error no park can clear. A parked loop has not ended; it is suspended.

**Source.** Loops, Runs, and Sessions, Loops and Their Outcomes.

**Look for.** The outcome type and every place an outcome is set; what a
bound, a cancel, an engine error, and a park write; any table of loops.

**Violation.** A sixth outcome, or a park written as an outcome; a bound
that ends a loop `failed`; a `loop_ended` step written for a parked
loop; a table of loops holding state the steps do not.

**Severity.** medium

**Check.** review

## STP-09 A session never ends, and its status is a projection

**Principle.** A session is a long-running record in the guideline's
sense: its loop work is a separate row, its sequence is its cursor, and
its park is its status. A loop ends; a session does not. An input that
wakes it starts a new loop, and an unlock resumes a parked one. Archived
and deleted are flags that can be undone, except the deletes that are
final. An archived session records arriving events without waking, and a
principal's message unarchives it. The status is a projection of the
steps, cached for queries. In code the session is `AgentSession`, never
the guideline's sign-in `Session`.

**Source.** Loops, Runs, and Sessions, A Session Never Ends.

**Look for.** The session entity, its status, and what sets it; the
transitions an input, an unlock, an archive, and a principal's message
make; the class name of the session.

**Violation.** A terminal session status that refuses a later input; a
status written by code that no step backs; an archived session that
drops an arriving event, or one an external event wakes; a session class
named `Session`.

**Severity.** medium

**Check.** review

## STP-10 Persist before you proceed

**Principle.** Every step is persisted before the engine acts on it. A
`model_request` is persisted before the call, and its response before
any tool runs. A `tool_request` is persisted, and audited, before the
policy decides and before the tool runs; its response is persisted
before the next model call. So a crash leaves at most one unanswered
request per call in flight, and the engine can be cancelled at any await
without losing state.

**Source.** Loops, Runs, and Sessions, Durable by Default.

**Look for.** The order of append, call, and run in the loop; whether
each append is awaited and committed before the next action; every
await between a tool's start and its request's append.

**Violation.** A tool started before its `tool_request` is committed, so
a crash leaves a run with no record and recovery runs it again; a model
call made before its request is appended; a tool run from a response not
yet persisted; an append fired without awaiting it.

**Severity.** high

**Check.** review

## STP-11 Recovery settles each open request by its tool's effect

**Principle.** A new run settles each unanswered request by what its
tool's effect allows. A stored response is reused. A model request with
no response is closed as abandoned and called again. A tool request
awaiting approval parks again. With no response, a `read_only` or
`idempotent` one runs again under the same idempotency key, the id of
its request step; an `unsafe` one is never repeated without the
transport's answer under that key, and is otherwise recorded as
interrupted, outcome unknown. A truncated tool-use block is never
executed, a request that enabled provider-side tools recovers like an
`unsafe` call, and a job started again attaches to the running one.

**Source.** Loops, Runs, and Sessions, Durable by Default; Tools, The
Tool Contract.

**Look for.** The recovery path of a new run and its branch per effect;
the key a re-run and a transport query use; how a truncated tool-use
block, a request with provider-side tools, and a started job recover.

**Violation.** An `unsafe` call run again after a crash with no outcome
from the transport; a re-run under a fresh key, so an idempotent tool
repeats its side effect; a truncated tool-use block executed; an unknown
outcome recorded as a plain failure the model may simply retry.

**Severity.** high

**Check.** review

## STP-12 A stale writer is refused, never trusted to stop

**Principle.** The guideline's fences hold the work row, never the
record, and two runs of one model call diverge. So each run takes a
writer epoch when it starts, larger than any before, on the session's
cursor row, before it reads the history. A compare-and-set on the cursor
row installs it, in the same role as the steps, never a read of the work
row. Every append from the run is conditional on that epoch, and every
command it sends carries it: a run that lost its claim cannot write a
step, and a transport refuses its commands.

**Source.** Loops, Runs, and Sessions, Durable by Default.

**Look for.** Where a run takes its epoch and how; the condition on every
step append; the epoch on every transport command and the transport's
check of it.

**Violation.** An epoch read from the work row, or taken after the
history is read; an append that does not condition on the epoch; a
transport command with no epoch, or a transport that runs a command
from a lower epoch.

**Severity.** high

**Check.** review

## STP-13 The history is the source of truth

**Principle.** A session's history is every step it recorded, in
sequence order. It only grows, until its retention purges it. Everything
derived from it is a query or a projection: the context a model reads,
the trace of a request, the cost of a loop, the replay after a crash,
the timeline a person scrolls, the agent's current plan, and the inputs
still pending. A projection may be cached, is always rebuildable, and is
never a second copy. Compaction changes what a model reads, never what
happened.

**Source.** History.

**Look for.** Every store of derived state: the plan, the pending
inputs, cost totals, the status, the timeline; whether each rebuilds
from the steps; what a compaction writes and removes.

**Violation.** A plan or a pending input whose only record is a table
of its own; a cost total no query over the steps reproduces; a
compaction that deletes or rewrites the steps it summarizes.

**Severity.** medium

**Check.** review

## STP-14 Each part lives in its role, and nothing crosses one

**Principle.** The session lives in the `core` role, its steps and
their cursor in `activity`, and its loop work in `queue`. A step append
is one atomic write in `activity`. What follows a step in another role,
waking a loop or announcing a change, is a write of the session with its
outbox rows, so nothing crosses a role. A step is durable before its
handoff, and the sweep wakes a session whose input is pending and whose
loop was never queued. A step is not an event: the session's status
changes and loop boundaries are announced, never each step.

**Source.** History.

**Look for.** The role of each table that holds the session, the steps,
the cursor, and the loop work; the writes that follow an append; what
the engine announces.

**Violation.** An append and a work-item insert in one transaction
across two roles; a loop enqueued straight from the append instead of
the session's outbox rows; an event per step.

**Severity.** medium

**Check.** review
