# Tools

Group id: `tools`. Covers Tools and The Runtime of
`agentic_core_spec.md`.

This group judges where the engine touches the world: the tool contract,
the registry, authorization classes, policy and approvals, execution and
jobs, the failures the model reads, secrets, and the runtime tools
execute in. It leaves the recovery of a tool call after a crash to
`steps`, the untrusted mark and the rule of two to `trust`, a park on an
approval or a job to `bounds`, the gate a spending job passes to
`bounds`, and the loud null transport of a session with no workspace to
`privacy`.

## TOL-01 Every tool declares its contract

**Principle.** Every tool declares a name and a description, which are
what the model sees and are versioned with the kind, since descriptions
are prompts; a typed input schema that refuses unknown fields; a typed
output, rendered to the model within a size bound; its own timeout; its
authorization class; its effect; whether it is interruptible; and its
mode, `sync` or `job`. A preflight is optional: it refuses a call that
cannot succeed before anyone is asked to approve it. Tools from native
code, MCP servers, and sub-agents take one contract.

**Source.** Tools, The Tool Contract; The Registry.

**Look for.** The tool declaration type and every tool built on it; the
input schema's handling of unknown fields; where descriptions live and
how they are versioned.

**Violation.** A tool with no class, no effect, no timeout, or no mode;
an input schema that accepts unknown fields; a description changed
outside the kind's version; a sub-agent or an MCP tool registered
through a second contract. (An effect declared safer than the tool is
TOL-02.)

**Severity.** medium

**Shape.** `scaffold/acme_root/om/src/acme/om/tools/types/tool.py` and
`scaffold/acme_root/om/src/acme/om/tools/tool.py`

**Check.** `agentic-check` decides that every `ToolSpec` built without
unpacking a mapping names its class, effect, timeout, interruptibility,
and mode; the rest is judged.

## TOL-02 A tool's effect says what a repeat may do

**Principle.** A tool's effect decides what may be repeated. `read_only`
reads. `idempotent` is safe to repeat, natively or under the idempotency
key. `unsafe` is a tool where a repeat may duplicate a side effect. The
declared effect is what a repeat of the tool would do, because recovery
and the engine's own retries act on it.

**Source.** Tools, The Tool Contract; Policy.

**Look for.** Each tool's declared effect beside what its code does; the
key an idempotent tool passes to the system it calls.

**Violation.** A tool that creates, sends, or charges declared
`read_only`, or declared `idempotent` with no key behind the repeat, so
recovery runs it twice (STP-11); a key the tool receives and drops.

**Severity.** high

**Shape.** `scaffold/acme_root/om/src/acme/om/tools/types/tool.py` and
`scaffold/acme_root/om/src/acme/om/tools/tool.py`

**Check.** review

## TOL-03 The registry is the agent's power

**Principle.** An agent's tool registry is its power: an agent without a
workspace tool cannot touch a workspace, whatever the model asks for. A
message to a session enqueues its loop, so the guideline's enqueue rule
applies to the registry: a principal may start or instruct a session of
a kind only if it may make each kind of call the registry offers. Policy
then gates each call on its own, since an agent picks its calls at
runtime.

**Source.** Tools, The Registry.

**Look for.** How a tool call from the model is resolved to a tool; the
permission check on starting or messaging a session; what that check
compares against the kind's registry.

**Violation.** A tool resolved from a name the model chose that the
registry does not hold; a session started or messaged by a principal who
may not make every kind of call its registry offers, so a message buys
calls its sender may not make.

**Severity.** high

**Shape.** `scaffold/acme_root/om/src/acme/om/tools/registry.py`

**Check.** review

## TOL-04 An MCP server's annotations are hints, never authority

**Principle.** Where tools come over MCP, each takes the same contract,
and the adopter's registry assigns its class and effect. The server's
own annotations, its read-only, destructive, and idempotent hints, are
hints, never authority. A server's tool definitions are pinned by hash,
and a changed definition is reviewed before the registry serves it.

**Source.** Tools, Tools over MCP.

**Look for.** Where an MCP tool's class and effect come from; the hash
pinned for each definition; what happens when a server's definition
changes.

**Violation.** A class or an effect read from a server's annotations; a
definition served with no pinned hash, or a changed one served before it
is reviewed.

**Severity.** high

**Shape.** `scaffold/acme_root/om/src/acme/om/tools/types/mcp.py` and
`scaffold/acme_root/om/src/acme/om/tools/mcp.py`

**Check.** review

## TOL-05 A class names the power a tool exercises

**Principle.** A tool's authorization class is the kind of power it
exercises, and what policy keys on: `read`, `write`, `execute`,
`network`, `integration`, `spawn`, `configuration`, `credentials`,
`destructive`, or a domain class a product adds. Running code in the
workspace is one class, `execute`, because inside a sandbox a build
script runs arbitrary code. `destructive` covers irreversible changes
outside the workspace.

**Source.** Tools, Authorization Classes.

**Look for.** Each tool's class beside what the tool does.

**Violation.** A tool that runs a command classed anything but
`execute`; a tool that fetches an arbitrary URL classed `read`; an
irreversible change outside the workspace, such as a force push, classed
`integration`.

**Severity.** medium

**Shape.** `scaffold/acme_root/om/src/acme/om/tools/types/tool.py`

**Check.** review

## TOL-06 Policy decides by class and target, never by the model's claims

**Principle.** A policy gives a call one of three decisions: allow,
require approval, or deny. It keys on the tool, its class, its effect,
and the attributes of what the call targets, never on what the model
says about the call. It is layered: the agent kind's defaults, narrowed
or loosened by the tenant, never past the platform's ceilings. What is
destructive, outward-facing, physical, or expensive waits for a person.
Preflight runs before the policy asks anyone.

**Source.** Tools, Policy.

**Look for.** The policy's inputs; how the kind's, the tenant's, and the
platform's layers combine; where preflight runs against the policy.

**Violation.** A decision that reads the model's stated reason, its
text, or a field of the tool input the model filled in to vouch for the
call; a tenant setting that loosens a call past a platform ceiling; a
destructive call allowed unattended.

**Severity.** high

**Shape.** `scaffold/acme_root/om/src/acme/om/tools/types/policy.py` and
`scaffold/acme_root/om/src/acme/om/tools/rules.py`

**Check.** review

## TOL-07 An approval is a person's decision on one exact call

**Principle.** An approval is a person's decision, never the model's.
Requiring one parks the loop, and other allowed calls from the same
response proceed. By default it binds to the exact call, the tool and a
hash of its input, so a changed input is a new call. A policy may grant
a class for the rest of the loop or until a deadline, never a
destructive or physical class, or bind an approval to a target chosen
later. The approver holds the approve permission for that class in the
tenant, and a policy may require someone other than the requester, or
two people. An approval expires. The decision is a control step.

**Source.** Tools, Approvals.

**Look for.** How an approval is requested, recorded, and matched to the
call it lets run; the hash it binds; its expiry; the approver's
permission check.

**Violation.** An approval the model or the agent's own step can give;
an approval matched by tool name alone, so a changed input runs on it; a
class grant for a destructive or physical class; an approval that never
expires.

**Severity.** high

**Shape.** `scaffold/acme_root/om/src/acme/om/tools/rules.py` and
`scaffold/acme_root/om/src/acme/om/tools/impl/manager.py`

**Check.** review

## TOL-08 A tool's time is the least of three, and its whole tree stops

**Principle.** Each tool call in a response is its own request and
response pair, and calls run concurrently when their tools allow it. A
tool's time is the least of its own timeout, the engine's limit, and the
time left before the tree's deadline. When it runs out, the tool's whole
process tree goes with it. Output streams while the tool runs.

**Source.** Tools, Execution.

**Look for.** How the time of a run is computed; what is stopped when it
runs out; whether concurrent calls are separate pairs.

**Violation.** A timeout that ignores the tree's deadline; a timeout
that stops the tool's process and leaves its children running; several
calls folded into one request step.

**Severity.** medium

**Shape.** `scaffold/acme_root/om/src/acme/om/tools/rules.py` and
`scaffold/acme_root/infra/src/acme/infra/transports/processes.py`

**Check.** review

## TOL-09 A job holds no runtime while it works

**Principle.** Where work outlives a run, a `job`-mode tool starts it
and returns a handle, and the loop parks on the job, releasing its
runtime. The job's completion arrives as an event that wakes the
session, and the tool response is written from it. A job carries its own
deadline, never later than the tree's, and cancelling the loop cancels
the job.

**Source.** Tools, Long-Running Jobs.

**Look for.** How a job is started, awaited, and completed; its deadline
against the tree's; what cancelling the loop does to it.

**Violation.** A runtime held open while a job works; a job with no
deadline (the hold it under-covers is BND-03), or one past the tree's; a
job left running after its loop is cancelled. (A second job started on
recovery is STP-11, and a spending job that skips the gate is BND-02.)

**Severity.** medium

**Shape.** `scaffold/acme_root/om/src/acme/om/tools/tool.py` and
`scaffold/acme_root/om/src/acme/om/tools/types/call.py`

**Check.** review

## TOL-10 A tool failure has a class the model reads

**Principle.** A tool failure has a class, decided where the failure
happens: `invalid_input`, `transient`, `timeout`, `denied`,
`interrupted`, or `permanent`. The model reads it with advice on what to
do next. A command that exits non-zero is a result, not a failure. The
engine retries on its own only a `transient` failure of a `read_only` or
`idempotent` tool; whether to repeat an unsafe call is the model's
decision. A model that repeats the same failing call is nudged after a
few repeats.

**Source.** Tools, Failures the Model Reads.

**Look for.** Where each failure class is set; how a non-zero exit is
returned; the engine's own retry and the effects it allows.

**Violation.** A failure classed far from where it happened, or with no
advice; a failing test returned as a tool failure; an engine retry of an
`unsafe` call (the repeated side effect is STP-11).

**Severity.** medium

**Shape.** `scaffold/acme_root/om/src/acme/om/tools/rules.py` and
`scaffold/acme_root/om/src/acme/om/steps/types/header.py`

**Check.** review

## TOL-11 A secret is brokered, or short-lived and scoped

**Principle.** A tool names a secret and never sees one in a prompt. The
first answer is a broker, an egress proxy or a credential helper outside
the sandbox, which attaches the credential per destination, so the
agent's process never holds it. When a secret must enter a process, it
is resolved for one call by whatever executes the call, short-lived, and
scoped so that `execute` cannot exceed its class, then injected into
that one process, whose environment was stripped of the engine's own
credentials first. The audit records the secret's name, never its
value. The injection is the engine's deviation from the guideline.

**Source.** Tools, Secrets Never Enter a Step; Deviations from the
Guideline.

**Look for.** How each tool reaches a credential; the scope and lifetime
of any secret placed in a process; the environment a tool process starts
with; what the audit records.

**Violation.** A secret's value in a prompt, a tool input, or a step; a
long-lived or broadly scoped token in a tool's process; a tool process
that inherits the engine's environment; an audit entry that holds a
value.

**Severity.** high

**Shape.** `scaffold/acme_root/infra/src/acme/infra/transports/injection.py` and
`scaffold/acme_root/infra/src/acme/infra/transports/local.py`

**Check.** `agentic-check` decides that no tool input, tool output, or step
type declares a `SecretStr` or `SecretBytes` field; the rest is judged.

## TOL-12 Redaction stops accidents; scope and lifetime are the defense

**Principle.** A secret in a process the agent controls is assumed
disclosed to the agent. Redaction of every output, raw, encoded, and
escaped, stops an accidental display before anything is persisted,
streamed, or shown to the model, and never a deliberate leak. Streams
redact with a holdback as long as the longest secret, so a value split
across two parts is still caught. A workspace snapshot is scanned for
secrets before it is pushed.

**Source.** Tools, Secrets Never Enter a Step.

**Look for.** Where redaction runs against persisting, streaming, and
rendering; the encodings it matches; the stream's holdback; the scan
before a snapshot is pushed.

**Violation.** Redaction after a step is persisted or a part is emitted;
a match on the raw value alone; a stream with no holdback, so a split
value passes; a snapshot pushed unscanned.

**Severity.** high

**Shape.** `scaffold/acme_root/infra/src/acme/infra/transports/redaction.py`

**Check.** review

## TOL-13 Every tool runs through the transport, and none reaches past it

**Principle.** Where tools execute is a dependency carried by two
capabilities. A workspace provider prepares, releases, and purges the
place an agent works, to an isolation spec: a mode, an egress policy,
and resource limits. An execution transport runs a command there,
streamed, and reads, writes, and lists files there. Every tool that runs
a command or touches a file goes through the transport, and none reaches
past it to the engine's own host. A host that only executes tool calls
needs no access to the history, the models, or the record. Both
capabilities live under `infra/`.

**Source.** The Runtime; The Object Model.

**Look for.** Every tool that runs a command or touches a file, and the
path it takes; any subprocess, file, or socket call in tool code outside
the transport; what a remote executor is given.

**Violation.** A tool that opens a file or starts a process on the
engine's host; an isolation spec with no egress policy or no limits; a
transport whose executor needs the history, a model, or the record to
run a command.

**Severity.** high

**Shape.** `scaffold/acme_root/om/src/acme/om/tools/tool.py` and
`scaffold/acme_root/infra/src/acme/infra/transports/__init__.py`

**Check.** `agentic-check` decides that a module defining a tool imports no
process, socket, or `shutil` module, and calls no `open()`, `os.open()`, or
`io.open()` and no `os` or `asyncio` function that starts a process; the
rest is judged.

## TOL-14 Isolation is refused, never weakened

**Principle.** Isolation is chosen up front and never weakened. A
workspace provider that cannot meet a session's isolation spec refuses
before the first model call; it never falls back to something weaker. An
environment may vanish between loops: an instance is released, and a
session is not. The next loop prepares another, and an
`environment_changed` step tells the model what changed under it.

**Source.** The Runtime.

**Look for.** What a workspace provider does when it cannot meet a spec;
when that check runs against the first model call; how a new environment
is announced.

**Violation.** A fallback from a VM to a container, or from a container
to a directory on the host, when the stronger mode is unavailable; a
refusal found after the model has been called; a new environment with no
`environment_changed` step.

**Severity.** high

**Shape.** `scaffold/acme_root/infra/src/acme/infra/workspaces/__init__.py` and
`scaffold/acme_root/om/src/acme/om/tools/manager.py`

**Check.** review
