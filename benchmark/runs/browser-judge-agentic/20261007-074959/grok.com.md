# grok.com

- URL: [redacted]
- Model: Expert
- Effort: none
- Sent: 2026-10-07T07:53:19Z
- Finished: 2026-10-07T07:57:39Z (the first of two polls a minute or more apart whose answer matched, with Enter voice mode beside the composer)
- Status: ok
- Score: 86/100
- Read: 3929a0ca5bf222772b04c465ec4aa78635bcefe7 (2026-10-06, release 0.7.0)
- Polls: 2
- Note: the answer was already done at the first poll, and the finish time is that poll's time, the first of the two polls that matched

## Answer

Score: 86/100

## Classification

It solves the problem of keeping a tool-call loop dependable after the model, the process, and the provider have all been replaced: durable, bounded, steerable, private, and auditable.
Its abstraction level is the engine under an agent, not the agent and not the fleet.
Inside an application it owns the loop, the append-only record, and the gates around every model call and tool call.
It owns steps, loops, runs, sessions, history, windows, model roles, the tool contract, policy, the runtime boundary, steering, attribution, agent kinds, budgets, parking, and session sealing.
It delegates general software design to the guideline, fleet placement and billing to `[redacted]`, and prompts, tools, and kinds to the product.
It is not an agent framework, a workflow language, or a product.
Category: durable agent-loop engine. That fits better than "agent framework" because the model is a replaceable decision-maker and the owned artifact is the loop's record and gates, and better than "workflow engine" because the control flow is one tool-call loop, not a user-authored graph.

## Adjacents

LangGraph, docs through 2026-09-28: checkpointed graph execution overlaps the loop, interrupts, and crash resume, not policy or privacy.
Claude Agent SDK, docs through 2026-10-05: the production tool loop overlaps turns, compaction, permissions, sub-agents, and session resume, not the record model.
Temporal, docs through 2026-10-06: event history and command/event replay overlap durability, fencing, and park-versus-fail, not the agent loop.

## Rubric

| Criterion | Measures | A 10/10 | Weight |
|---|---|---|---|
| Conceptual model | Whether the nouns and invariants compose without special cases | A few nouns, each with one job, and invariants a reviewer can check | 12 |
| Separation of concerns | Whether engine, model, product, and platform stay on their own side of the boundary | Each concern has one owner; nothing leaks across | 10 |
| Control flow and durability | Crash-safe progress, effect-aware recovery, stale-writer fencing, and "not now" distinct from "failed" | Persist before act; recovery decided by effect; a lost writer cannot write | 12 |
| State and context | History as truth, windows as views, deterministic render, compaction that does not rewrite the past | Append-only truth; every view rebuildable; render a pure function | 10 |
| Tool and policy model | The action contract, and whether policy can ignore what the model claims | Class, effect, and target decide; approvals bind an exact call | 10 |
| Trust and isolation | Authority, instruction versus data, secrets, and where tools run | The agent holds no authority; secrets never enter the record; isolation is refused, not weakened | 10 |
| Provider independence | Whether a call site names a task, and a model can be swapped without a silent change | One content shape; an explicit switch; a second adapter proves the boundary | 8 |
| Bounds and failure semantics | Spend, time, and progress limits, and what each trip means | One gate before the call; worst case held; fail closed; park, bound, and yield are different | 8 |
| Observability and testability | Whether the engine can be watched, audited, and re-run without the model | Shape-only traces; a scripted provider; replay checks the render | 8 |
| Extension contracts | How an adopter adds a tool, a kind, or a dependency without forking the loop | Injected interfaces; a missing capability refuses loudly | 6 |
| Operational coherence | Whether concurrency, cancellation, and the host model are specified in proportion to the guarantees | Cancellation and fencing specified; complexity matches the invariants | 6 |

## Scores

| Criterion | Weight | agentic_core | LangGraph | Claude Agent SDK | Temporal |
|---|---|---|---|---|---|
| Conceptual model | 12 | 9 | 7 | 7 | 9 |
| Separation of concerns | 10 | 9 | 6 | 6 | 8 |
| Control flow and durability | 12 | 9 | 6 | 5 | 9 |
| State and context | 10 | 9 | 6 | 7 | 8 |
| Tool and policy model | 10 | 9 | 4 | 7 | 4 |
| Trust and isolation | 10 | 8 | 3 | 6 | 4 |
| Provider independence | 8 | 8 | 7 | 3 | 7 |
| Bounds and failure semantics | 8 | 9 | 4 | 6 | 6 |
| Observability and testability | 8 | 8 | 6 | 6 | 9 |
| Extension contracts | 6 | 8 | 7 | 7 | 8 |
| Operational coherence | 6 | 7 | 7 | 7 | 6 |
| Weighted result | 100 | 86 | 57 | 61 | 72 |

Scores measure this rubric, not which system is better at its own job. LangGraph and the Claude Agent SDK do not specify trust or effect-classified recovery; Temporal does not specify an agent loop. Those gaps are scope, and they are marked below.

## agentic_core, criterion by criterion

Conceptual model scores 9. The Core and Concepts at a Glance fix a small set of nouns: a step is one event, a loop is a span of steps with no table, a run is one stretch on one runtime, a session is the entity that never ends. A response points at its request (`Steps`, One Event, One Step). A park is not one of the five outcomes (`Loops and Their Outcomes`: `succeeded`, `failed`, `inconclusive`, `cancelled`, `errored`). The hold is that "a session never ends" sits beside archive, mark-deleted, key revocation, and purge (`A Session Never Ends`, `Three Deletes`), which a reader can mistake for an end.

Separation of concerns scores 9. `Scope` puts general design in the guideline, the fleet in `[redacted]`, and prompts and kinds in the product. `The Engine and the Brain` states the split: the model chooses, the engine does, and durability, bounds, approvals, and the record are engine semantics. The engine is a set of injected interfaces. The hold is the scaffold: `scaffold/README.md` ships a whole monorepo (API, portal, Terraform) as "the domain-free core," so the delivered boundary is wider than the spec's. Economy also edges into what a kind's prompts may contain.

Control flow and durability scores 9. `Durable by Default` persists the request before the call and the tool request before policy and execution, recovers from a table keyed by effect, and uses the request step id as the idempotency key. An `unsafe` call with no transport answer is recorded `interrupted` and not repeated. A writer epoch is a compare-and-set on the cursor row; a lost run cannot append, and the transport refuses its commands. The loop interface in `scaffold/acme_root/om/src/acme/om/agents/loop.py` matches that. Held back because the transport's outcome protocol is "can answer," concurrent tool partial failure is not a matrix in the spec, and two recovery rules live only in an agents-only comment.

State and context scores 9. `History` makes the append-only step log the source of truth and every other reading a projection. `Rendering` makes a request a pure function of kind version, fill, pinned zone, window, and new inputs, laid out stable-to-volatile, with a prompt hash on the request. `Windows` keeps the window a property of a request, not of the history. `Compaction` changes what is read, never what happened, and only principals feed the pinned zone. Held back because the summary itself is a model call, so replay checks the hash, not the summary's content, and provider cache breakpoints are named rather than contracted.

Tool and policy model scores 9. `The Tool Contract` requires a class and an effect (`read_only`, `idempotent`, `unsafe`). `Policy` decides allow, approve, or deny from tool, class, effect, and target, never from the model's claims. An approval binds the tool and a hash of its input (`Approvals`). MCP annotations are hints (`Tools over MCP`). `The Runtime` refuses an isolation spec it cannot meet. Held back by the class-grant exception, optional preflight, and domain classes with no composition rule beyond "the product adds them."

Trust and isolation scores 8. `Who Is Who` splits actor, principal, and spender; the agent is an actor and holds no authority. `Only a Principal Instructs` renders everything else as labelled data. The untrusted mark is sticky and follows the rule of two (`Bound What a Convinced Model Can Do`). `Secrets Never Enter a Step` assumes disclosure once a secret is in the agent's process and prefers a broker. Held back because `What This Spec Does Not Cover` defers the threat model and erasure of one person inside a shared session, and "outward" versus "own work product" is illustrated rather than defined. Secret injection is a recorded deviation.

Provider independence scores 8. `Roles and Fills` makes every call site name a model role; a fill is provider, model, effort, and eligibility. A switch is a new fill-set version and a `switched` step (`Fill Sets and Switches`). `The Provider Boundary` demands one content shape and treats a second adapter that runs the loop unchanged as the proof. The scaffold has Anthropic and OpenAI adapters plus a scripted twin. Held back because thinking and cache markers are defined as not surviving translation, and the spec explicitly leaves wire schemas out of scope.

Bounds and failure semantics scores 9. `One Gate, Before the Call` authorizes every model call and spending job against worst-case exposure, holds it, and settles at reported usage or the full hold unless the provider provably did not bill. An unknown spender spends nothing. `Time, Steps, and Streaks` separates a guard that parks, a bound that ends the loop `inconclusive`, and a yield that hands the run back; the step guard cannot be disabled. `Parking` names reason, unlock, and retry time, and holds no lease. Held back because a model priced by a default row is admitted to turn budgets into guesses, and the shared ledger is the platform's.

Observability and testability scores 8. `Streams` makes a part a numbered fragment of a step, never a step, and forbids emission from blocking the loop. `Tracing` makes a trace a query and spans shape-only. `Testing and Conformance` specifies a scripted provider, prompt-hash replay, a conformance kit, and a fake clock. The scaffold has contract tests for step storage, windows, and budgets. Held back because judging a kind is delegated, and stream ordering under concurrent tools is not specified beyond numbering.

Extension contracts scores 8. `The Engine and the Brain` injects storage, keys, ledger, providers, transport, clock, and stream sink. `Null Objects` splits quiet observers from loud capabilities and forbids a quiet no-op that reports a success. An agent kind is a versioned profile over the one loop (`Agent Kinds`). Held back because the spec says its Python is never an API, so the contract is the scaffold, and MCP is optional.

Operational coherence scores 7. The epoch fences the record and the work lease fences the worker (`Durable by Default`), a park holds no runtime (`Parking`), and a yield returns the loop to the queue. That split is coherent. Held back because an adopter inherits four database roles, an outbox, a key service, and a workspace provider from the guideline scaffold, and cancellation of a concurrent tool tree plus a non-blocking stream is specified as a principle more than as a failure matrix.

## Where it differs

Stronger: effect-classified recovery plus a writer epoch (`Durable by Default`) refuses a repeated unsafe call and a stale writer; LangGraph's sync checkpoint still re-executes the node, and the Claude Agent SDK resumes a transcript without an effect table.
Stronger: policy keys on class and target, never on the model's claim (`Policy`); the Claude Agent SDK's `auto` mode lets a model classifier allow or block, and LangGraph leaves the gate to the application.
Stronger: a park is not an outcome and names its unlock (`Parking`, `Loops and Their Outcomes`); the Claude Agent SDK ends on `max_turns` or budget, and LangGraph interrupts without a reason taxonomy.
Weaker: Temporal's command/event history and replay determinism (`How Temporal works`, docs 2026-10-06) make workflow progress a function of the log; `agentic_core` replays the render hash, not the model's choice, and leaves the transport outcome query underspecified.
Weaker: the Claude Agent SDK's permission modes and hooks are a smaller operational surface than four database roles plus a key service; the guarantee is weaker, and the host is simpler.
Broader: `agentic_core` owns sealing, the spender, and the budget hold (`Privacy`, `One Gate, Before the Call`); all three adjacents leave those to the application. Temporal is broader on arbitrary workflows and child-workflow lifecycle.
Narrower: one loop, not a user-authored graph (LangGraph) and not a general workflow language (Temporal). Compaction and sub-agent context isolation overlap the Claude Agent SDK; the record model does not.
Different: history is the truth and the window is a view (`History`, `Windows`), against LangGraph's checkpoint snapshot and the Claude Agent SDK's compacted transcript. The view wins when audit must survive compaction; the snapshot wins when the application wants time-travel of mutable state.
Different: a call names a role (`Roles and Fills`), against the Claude Agent SDK's Claude-shaped loop. The role wins when the model must be swapped mid-session; the Claude loop wins when the tool set and the model are one product.

## What I would change

Lift the transport outcome query and the concurrent-tool failure matrix out of the agents-only comment into `Durable by Default`, which moves control flow from 9 toward 10 and operational coherence from 7 toward 8.
Define "outward" and "own work product" in `Bound What a Convinced Model Can Do`, and say that a threat model is required of the adopter rather than deferred, which moves trust from 8 toward 9.
State in `The Repository` that the engine is the agent namespaces and the session runner, and that the rest of the scaffold is the guideline's host, which moves separation from 9 toward 10 and operational coherence from 7 toward 8.
Make the content shape in `The Provider Boundary` a normative schema instead of an out-of-scope wire format, which moves provider independence from 8 toward 9 and extension contracts from 8 toward 9.
Reject a fill priced by a default row at resolve time in `Usage and Cost`, which moves bounds from 9 toward 10.
Specify stream part ordering and cancellation across concurrent tools in `Streams` and `Execution`, which moves observability from 8 toward 9.

## Method

Read `agentic_core` at `3929a0ca5bf222772b04c465ec4aa78635bcefe7` (2026-10-06, release 0.7.0): the whole spec, README, changelog, lens format, and the loop interface plus the scaffold layout.
Read LangGraph checkpointers and durable-execution docs dated 2026-05-25 through 2026-09-28; the durability score rests on those excerpts because the durable-execution page fetch returned the persistence page instead.
Read the Claude Agent SDK agent-loop, sessions, and sub-agents docs dated 2026-09-28 through 2026-10-05; crash recovery and privacy are scored only from what those pages state.
Read Temporal's how-it-works and event-history docs dated 2026-10-06; the trust score is low confidence because payload encryption and frontend auth were not read.
