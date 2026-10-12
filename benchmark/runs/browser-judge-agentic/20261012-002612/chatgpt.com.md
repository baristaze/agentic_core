# chatgpt.com

- URL: [redacted]
- Model: GPT-6
- Effort: High
- Sent: 2026-10-12T00:27:23Z
- Finished: 2026-10-12T00:36:50Z (the poll that saw "Worked for 5m 22s" above the answer)
- Status: ok
- Score: 83/100
- Read: main, accessed October 11, 2026, its changelog at release v0.16.0; no commit named
- Polls: 3
- Note: the page opened in Work mode and was switched to Chat; the model list's top entry reads GPT-6 where sizes.yaml (2026-09-27) names the flagship Latest, and GPT-6 was clicked; the run's tab group closed at about 00:31 while the answer was being written, and the conversation was reopened from its URL

## Answer

Score: 83/100

## Classification

What it is: A durable, policy-governed execution engine for long-running AI agents, with an accompanying reference implementation and architectural conformance system.

Architectural problem: How to let an agent execute over hours, days, or months while maintaining coherent state, surviving interruptions, controlling side effects, enforcing authority boundaries, and accounting for resource consumption.

Abstraction level: Above model SDKs and individual tool implementations, but below distributed agent platforms and application-specific workflows.

Architectural role: The execution kernel embedded inside an application or agent-hosting platform.

Core execution model: An append-only history of typed steps, interpreted through loops, runs, and persistent sessions. The model makes decisions; the engine governs execution.

Primary abstractions: `Step`, `Loop`, `Run`, `AgentSession`, `ContextWindow`, `Fill`, `Tool`, `ToolRuntime`, `AgentKind`, `Principal`, `Budget`, and `Park`.

What it owns: Execution history, context construction, model invocation, tool policy, failure recovery, lifecycle transitions, steering, delegated authority, execution bounds, and session privacy.

What it delegates: Fleet scheduling, infrastructure placement, platform billing, product-specific behavior, concrete external integrations, and the general software architecture conventions inherited from SWE Guidelines.

What it is not: A prompt orchestration library, model abstraction SDK, arbitrary workflow graph engine, distributed task scheduler, or complete hosted agent platform.

Repository form: An architectural specification, implementation scaffold, conformance lenses, static checker, and agent-development guidance. The repository root is explicitly not a distributable Python package.

Category: Durable Agent Execution Kernel

This category fits better than agent framework. Its distinguishing abstraction is not how to define an agent, but how to execute that agent safely, durably, and coherently across failures and time. The closest conceptual neighbor is a specialized durable-execution engine with an agent-native control plane.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://raw.githubusercontent.com\&sz=32)

raw\.githubusercontent.com

+3



The design's most important architectural proposition is:

The model owns decisions. The engine owns the consequences of executing those decisions.

That separation drives nearly every major design choice.

## Adjacents

I selected three systems based on architectural overlap, not adoption.

| System                | Why it belongs                                                                                                                                            | Architectural overlap                                                                                                    |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| Pydantic AI + Harness | The closest agent-runtime comparator, particularly when its durable-execution, persistence, workspace, and spending capabilities are considered together. | Agent loop, typed models/tools, model independence, execution capabilities, persistence, streaming, workspaces, budgets. |
| LangGraph             | The closest stateful agent-orchestration comparator, emphasizing checkpointed execution and explicit control flow.                                        | Persistent state, recovery, interrupts, streaming, subgraphs, human steering, execution composition.                     |
| Temporal              | The strongest comparison for durable execution semantics independent of agent-specific machinery.                                                         | Event histories, replay, crash recovery, concurrency, workflow lifecycle, timers, external effects.                      |

Pydantic AI now specifies substantially more than typed model calls. Its Harness includes append-only step events, tool-effect tracking, spend limits, and workspace abstractions; durable execution can be delegated to engines including Temporal. LangGraph uses checkpointed graph state and explicit interrupts. Temporal reconstructs execution by replaying deterministic workflow code against its event history.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub

+5



Scope normalization: The rubric measures architecture for long-running, tool-using agents. A framework is not considered defective merely because an application must supply a capability outside its intended scope. However, the need to compose and enforce that capability affects the relevant criterion. This matters especially for Temporal.

I did not select Claude Agent SDK or OpenAI Agents SDK because these three systems provide a more useful spread of architectural overlap: agent-native execution, stateful orchestration, and durable workflow execution.

## Rubric

The following rubric is defined for the category Durable Agent Execution Kernel. It is frozen for all four evaluations.

| Criterion                       | What it measures                                                                   | What earns 10/10                                                                               | Weight |
| ------------------------------- | ---------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- | ------ |
| 1. Conceptual model             | Abstraction quality, ownership, responsibility boundaries                          | Minimal, orthogonal concepts; unambiguous ownership; no contradictory semantics                | 12     |
| 2. State and context            | Persistent history, contextual projections, model-facing state                     | Complete, reconstructible state with deterministic, efficient context rendering                | 11     |
| 3. Durability and concurrency   | Crash recovery, effect safety, writer ownership, race handling                     | Every interruption has a defined recovery; no duplicate unsafe effects or stale writes         | 16     |
| 4. Execution lifecycle          | Control flow, suspension, resumption, steering, child execution                    | Explicit, composable, complete transitions with no ambiguous lifecycle states                  | 9      |
| 5. Model and tool contracts     | Provider independence, tool abstractions, extensibility, type contracts            | Strong portable contracts with explicit capabilities, failures, and safe extension points      | 11     |
| 6. Trust and isolation          | Authority, untrusted inputs, approvals, workspace and secret boundaries            | Authority cannot be forged or escalated; isolation and security assumptions are enforced       | 13     |
| 7. Budget and bounds            | Resource ceilings, cost accounting, limits across execution scopes                 | Concurrent and uncertain spending is bounded before execution, with attributable accounting    | 9      |
| 8. Streaming and observability  | Progress events, state inspection, debugging, operational evidence                 | Consistent, recoverable, interoperable visibility without coupling consumers to execution      | 6      |
| 9. Determinism and verification | Replay, testing, conformance, invariant verification                               | Reproducible execution with comprehensive failure, adapter, and concurrency verification       | 8      |
| 10. Coherence and adoption      | Architectural simplicity, interoperability, operational and integration complexity | A small, comprehensible core with clear integration contracts and minimal incidental machinery | 5      |
| Total                           |                                                                                    |                                                                                                | 100    |

For scoring, 9 means unusually strong architecture, not simply broad feature coverage. A 10 requires exceptional completeness for the criterion.

## Scores

All criterion scores are out of 10. Weighted results use Σ(score × weight) / 10, rounded to the nearest whole number.

| Criterion                    | Weight | agentic_core | Pydantic AI + Harness | LangGraph | Temporal |
| ---------------------------- | ------ | ------------ | --------------------- | --------- | -------- |
| Conceptual model             | 12     | 9            | 9                     | 9         | 9        |
| State and context            | 11     | 9            | 8                     | 8         | 7        |
| Durability and concurrency   | 16     | 8            | 8                     | 8         | 10       |
| Execution lifecycle          | 9      | 8            | 8                     | 9         | 9        |
| Model and tool contracts     | 11     | 8            | 9                     | 8         | 7        |
| Trust and isolation          | 13     | 9            | 7                     | 6         | 6        |
| Budget and bounds            | 9      | 9            | 7                     | 4         | 4        |
| Streaming and observability  | 6      | 7            | 9                     | 9         | 8        |
| Determinism and verification | 8      | 8            | 8                     | 8         | 9        |
| Coherence and adoption       | 5      | 6            | 9                     | 8         | 7        |
| Weighted result              | 100    | 83/100       | 81/100                | 77/100    | 77/100   |

These are assessments of architectural quality for the selected category, not claims that one system is universally preferable. Temporal is particularly strong where durable execution is the dominant requirement. Pydantic AI is stronger for embedding a composable agent SDK into an existing application. LangGraph is stronger for explicitly constructed, branching execution graphs.

The relatively narrow gap between `agentic_core` and Pydantic AI is intentional: their current documented architectural capabilities overlap considerably more than the familiar descriptions of these projects suggest.

## agentic_core, criterion by criterion

### 1. Conceptual model: 9/10

The strongest design decision is the separation between `Session`, `Loop`, `Run`, and `Step`.

In `agentic_core_spec.md`, Concepts at a Glance, The Engine and the Brain, and Loops, Runs, and Sessions establish different responsibilities for durable continuity, logical execution, physical execution attempts, and recorded facts. This prevents an agent's conversational lifetime from being confused with a worker's process lifetime.

A particularly good choice is treating history as the source of truth and session status as a projection, rather than maintaining two independent authorities.

The distinction between model decisions and engine-enforced policies is equally important. It prevents the model's output from becoming an implicit authorization to execute.

The deduction comes from the large conceptual surface. Execution, privacy, identity, workspaces, subagents, budgets, and persistence interact through several managers. The nouns are individually well-defined, but their combined behavioral contract is harder to reason about than the conceptual model initially suggests.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://raw.githubusercontent.com\&sz=32)

raw\.githubusercontent.com

+2



Assessment: Excellent conceptual foundations, with complexity concentrated at the boundaries between abstractions.

### 2. State and context: 9/10

The Steps, History, and Context sections make an important distinction: recorded history is not the prompt sent to the model.

History is append-only; model-facing context is a deterministic projection with pinned instructions, provider-specific formatting, explicit compaction, summaries, and artifact references.

The scaffold substantiates this architecture through `om/steps/storage/impl/postgres.py`, `om/windows/impl/manager.py`, and `om/tests/unit/test_windows.py`.

The PostgreSQL implementation uses a cursor row to allocate contiguous step sequences under a lock. The window implementation is separately responsible for rendering and compaction. Tests cover prompt hashes, overflow behavior, summaries, and artifacts.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://raw.githubusercontent.com\&sz=32)

raw\.githubusercontent.com

+3



This is stronger than treating an LLM's message array as the canonical state.

Remaining ambiguity concerns the evolution of historical projections. Kind versions and recorded prompt hashes help, but the architecture would benefit from a more explicit compatibility contract for changes to renderers, compaction strategies, and provider-specific serialization.

Assessment: A strong event-sourced design with unusually good separation between historical truth and model context.

### 3. Durability and concurrency: 8/10

The Durable by Default section contains some of the repository's most valuable architecture.

Model and tool requests are persisted before execution. Recovery distinguishes read-only, idempotent, and unsafe effects. An unsafe call with an unknown outcome is not blindly repeated. Writer epochs fence stale runners, including their storage appends and transport commands.

These are not merely conceptual statements. `om/agents/impl/loop.py` contains concrete request/recovery paths, while `om/steps/storage/impl/postgres.py` implements conditional, epoch-fenced appends.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://raw.githubusercontent.com\&sz=32)

raw\.githubusercontent.com

+3



The design also correctly recognizes that an idempotency key does not magically make an external operation idempotent. Recovery depends on the tool's declared semantics and the transport's ability to resolve outcomes.

The important gap is completeness of the combined state-transition contract.

The specification documents individual recovery cases well, but it does not yet establish one exhaustive, verifiable transition model covering every combination of in-flight calls, lost writers, cancellations, concurrent tool results, approvals, and newly arriving inputs.

For example, cancellation of a local coroutine does not prove that a remote effect stopped. The spec recognizes uncertain effects, but the total relationship among cancellation, settlement, fencing, and resumed execution warrants stronger formal treatment.

I also cannot establish from source inspection alone that every adapter preserves the epoch and idempotency guarantees.

Assessment: Very strong failure-aware design. The remaining work is proving its invariants across every execution boundary, rather than improving its basic primitives.

### 4. Execution lifecycle: 8/10

The Loops, Runs, and Sessions, Parking, Steering, and Agent Kinds and Sub-Agents sections define a sophisticated lifecycle.

A session survives multiple loops. A run is one uninterrupted execution attempt. Parking is distinct from failure. A parked loop may resume when its blocking condition clears, while an idle session may receive new inputs and begin another loop.

The design distinguishes five terminal loop outcomes: `succeeded`, `failed`, `inconclusive`, `cancelled`, and `errored`.

The scaffold's `om/agent_sessions/impl/manager.py` and `om/agents/impl/loop.py` implement these concepts, including controls, child execution, and continuation.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://raw.githubusercontent.com\&sz=32)

raw\.githubusercontent.com

+3



The model is more appropriate for long-running autonomous agents than a simplistic `running/completed/failed` state machine.

However, its flexibility creates transition complexity. An event can arrive while a tool is running, while a session is parked, during a parent-child handoff, or as a worker loses its claim.

The documented lifecycle graph is useful, but it is not itself a complete transition specification.

Assessment: Strong lifecycle semantics, particularly parking and continuation. An explicit total transition function would strengthen the design considerably.

### 5. Model and tool contracts: 8/10

The Models section introduces model roles and versioned fills instead of binding an agent kind directly to a provider or model name.

This is a good separation. Agent behavior names a capability; deployment policy resolves that capability into a concrete model with its own context window, effort, retention, and provider constraints.

The Tools section is similarly deliberate. Each tool declares not just an input schema but authorization class, effect semantics, execution mode, and relevant limits.

The October 11 release adds versioned toolsets, deferred loading, role-specific model sizes and efforts, and MCP tool definitions checked against pinned identities. The scaffold includes concrete provider adapters and tool managers.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://raw.githubusercontent.com\&sz=32)

raw\.githubusercontent.com

+3



Two limitations hold the score below 9.

First, the design favors broad provider neutrality, but nonportable capabilities such as provider-managed reasoning state, caching, and provider-executed tools still need careful, explicit adaptation.

Second, the repository does not provide a separately versioned, distributable runtime API comparable to Pydantic AI's typed Python interfaces. That is an intentional repository form, but it makes independent integration and contract evolution more demanding.

Assessment: Excellent architectural direction, with some remaining portability and integration friction.

### 6. Trust and isolation: 9/10

This is one of the strongest areas.

The Identity, Trust, and Attribution section separates three concepts frequently conflated in agent systems:

- Actor: Who performed the action?
- Principal: Whose authority permits it?
- Spender: Whose resources pay for it?

The agent is an actor, not an independent source of authority.

The specification further distinguishes instructions from untrusted data, requires tool authorization to be evaluated outside model-generated text, and refuses execution environments that cannot satisfy declared isolation requirements.

It also specifies scoped secrets, approval binding, session-specific encryption, and explicit privacy limitations. The corresponding tool, attribution, and privacy managers exist in the scaffold.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://raw.githubusercontent.com\&sz=32)

raw\.githubusercontent.com

+4



This is a better conceptual security model than relying on prompt instructions to keep agents inside their boundaries.

The remaining concern is enforcement across the entire runtime environment. Workspace isolation alone does not establish network egress policy, and key revocation cannot erase copies already transmitted to external systems. The specification acknowledges important parts of this problem.

Assessment: Excellent security architecture for an agent execution kernel, although its guarantees ultimately depend on correctly configured platform adapters.

### 7. Budget and bounds: 9/10

The Bounds and Budgets section treats resource control as an execution precondition rather than a reporting feature.

Model calls pass a gate before they begin. The gate reserves a conservative amount, and settlement reconciles actual or uncertain usage. Child agents share inherited bounds, while deadlines and step limits prevent an unbounded execution tree.

These contracts appear in the scaffold's budget and model-call integration paths.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://raw.githubusercontent.com\&sz=32)

raw\.githubusercontent.com

+2



A particularly strong decision is retaining conservative accounting when actual usage cannot be determined.

This differs meaningfully from Pydantic AI Harness's documented `SpendLimits`, which tracks cross-run and per-tenant spending but explicitly allows the request that crosses a ceiling to complete. Concurrent requests can also pass before their spending is recorded.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://pydantic.dev\&sz=32)

Pydantic Docs



The deduction is for the complexity of enforcing exact, conservative accounting across providers, tool jobs, retries, and cancellation. The architecture recognizes these cases, but exhaustive reconciliation behavior is difficult to establish without executing failure-injection tests.

Assessment: Unusually strong agent-native resource governance.

### 8. Streaming and observability: 7/10

The Streams and Steering sections distinguish transient presentation events from durable historical facts.

Streaming should not block execution, and a failed client connection should not change the agent's outcome. Meanwhile, important model/tool requests and responses remain represented in persistent history.

This is a sound separation between execution correctness and UI responsiveness. The implementation includes streaming sinks, persisted steps, event delivery, and observability infrastructure.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://raw.githubusercontent.com\&sz=32)

raw\.githubusercontent.com

+3



The weakness is the lack of a sufficiently precise external observability contract.

The architecture would benefit from a stable event schema describing how operators correlate sessions, loops, runs, model requests, tool attempts, budget holds, and external effects.

It would also benefit from clearer reconnect and projection semantics for downstream consumers. An append-only history provides the necessary foundation, but it is not automatically a complete operational event API.

Assessment: Correct separation of durable state from transient streams; less complete as an interoperability and operational-diagnostics design.

### 9. Determinism and verification: 8/10

The Testing and Conformance section calls for scripted providers, deterministic clocks and identifiers, replay with prompt-hash verification, and interface conformance tests.

These are supported by real implementation material.

`om/tests/unit/test_windows.py`, for example, uses a scripted provider and explicitly tests compaction, prompt hashes, and budget interaction. The `lenses/` directory supplies reviewable invariants, while `agentic-check` statically enforces the subset that can be decided reliably from code.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://raw.githubusercontent.com\&sz=32)

raw\.githubusercontent.com

+3



Separating syntactic checks from reviewer judgment is a good design decision. The checker does not pretend to prove semantic correctness from source patterns.

The primary limitation is evidence of exhaustive concurrency and recovery verification. The inspected tests demonstrate meaningful contract coverage, but they do not establish that every crash boundary, interleaving, and adapter combination has been verified.

The next step should be systematic state-machine and failure-injection testing, not additional narrative conformance rules.

Assessment: Strong verification architecture, with room to turn critical invariants into executable completeness claims.

### 10. Coherence and adoption: 6/10

The object model is carefully organized into namespaces, interfaces, implementations, and explicit composition roots.

That is a genuine strength.

However, the actual adoption surface is substantial.

The scaffold inherits a full domain-agnostic, multi-tenant application foundation with tenancy, databases, queues, outbox, workers, APIs, clients, deployment infrastructure, and operational tooling.

`om/root.py` composes a large graph of managers, and `om/agents/impl/loop.py` coordinates numerous concerns inside one execution implementation.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://raw.githubusercontent.com\&sz=32)

raw\.githubusercontent.com

+2



This creates a tension between two desirable goals:

1. Providing a complete, opinionated reference system.
2. Making the execution kernel independently embeddable.

The first is accomplished much more convincingly than the second.

This is not a criticism of architectural scope. The problem is that a relatively narrow execution kernel has a comparatively broad integration and operational footprint.

The current structure is well suited to teams adopting the entire architectural posture. It is less convenient for teams that want to embed the engine inside an existing application.

Assessment: Internally coherent but externally expensive to adopt.

## Where it differs

### Stronger

- Than Pydantic AI on pre-execution spending control: `agentic_core` treats budget reservation as a mandatory execution gate, rather than accepting a bounded amount of budget overshoot. This provides a stronger foundation for hard spending ceilings.&#x20;

  [image](https://www.google.com/s2/favicons?domain=https://raw.githubusercontent.com\&sz=32)

  raw\.githubusercontent.com

  +1

- Than LangGraph on agent-native authority semantics: Separating actor, principal, and spender, and binding authorization to tool classes and targets, makes trust an execution-kernel concern rather than primarily an application responsibility.&#x20;

  [image](https://www.google.com/s2/favicons?domain=https://raw.githubusercontent.com\&sz=32)

  raw\.githubusercontent.com

  +1

- Than generic workflow execution on agent-specific recovery: The explicit distinction between model requests, tool effect classes, unknown external outcomes, and model-visible verification is tailored to autonomous tool-using agents.

### Weaker

- Than Temporal on durable execution verification: Temporal has a deeper deterministic workflow replay contract, including command-history matching, activity boundaries, and workflow code versioning. `agentic_core` has strong recovery rules but a less completely demonstrated execution-state correctness model.&#x20;

  [image](https://www.google.com/s2/favicons?domain=https://docs.temporal.io\&sz=32)

  Temporal Documentation

- Than Pydantic AI on composability and typed developer interfaces: Typed outputs, dependency injection, capabilities, and external durability backends provide a smaller integration footprint without requiring adoption of a broad application scaffold.&#x20;

  [image](https://www.google.com/s2/favicons?domain=https://pydantic.dev\&sz=32)

  Pydantic Docs

  +1

- Than LangGraph on explicit control-flow composition: Its graph and node abstractions make branching, joining, and workflow inspection more directly programmable than `agentic_core`'s opinionated model-led execution loop.&#x20;

  [image](https://www.google.com/s2/favicons?domain=https://docs.langchain.com\&sz=32)

  Docs by LangChain

  +1



### Broader or narrower

- Broader than a conventional agent SDK: `agentic_core` owns execution governance, resource accounting, durable history, privacy, delegated authority, and lifecycle semantics that simpler SDKs can leave to applications.
- Narrower than LangGraph: It does not attempt to expose arbitrary graph topology as its primary programming model.
- Narrower than Temporal: It does not own general distributed workflow scheduling and execution for arbitrary applications.
- Broader in its reference scaffold than in its stated kernel boundary: The scaffold supplies a multi-tenant application environment, while the architectural claim is specifically about the agent execution engine.

### Simply different

- Event history vs. graph checkpoints: `agentic_core` privileges append-only facts and derived views; LangGraph privileges checkpointed graph state. The former emphasizes auditability and reconstructibility, while the latter makes explicit graph-state inspection and time travel more natural.
- Agent-native recovery vs. workflow replay: `agentic_core` interprets persisted model/tool events using effect-aware rules. Temporal replays deterministic workflow code against recorded events. Both are defensible, but they solve consistency at different abstraction levels.
- Opinionated safety vs. configurable execution: `agentic_core` restricts certain behaviors to preserve invariants; composable SDKs generally allow the application to assemble its own policies. The former wins when uniform guarantees matter more than integration flexibility.

## What I would change

The following are ranked by their architectural value. Improvements are estimates on the affected criterion's 10-point scale, not promised score increases.

1\. Make execution-state completeness an explicit invariant. In `agentic_core_spec.md`, Loops, Runs, and Sessions, define a total transition table over lifecycle state, input/control event, in-flight effects, writer epoch, and resulting persisted state. Every accepted event should have a known durable successor or an explicit typed refusal. Durability +1; Lifecycle +1.

2\. Turn recovery invariants into executable state-machine tests. Extend scaffold conformance with crash injection and interleaving tests at every persistence, dispatch, settlement, cancellation, and park boundary. Validate that stale writers cannot append, unsafe calls cannot repeat without evidence, and every recoverable interruption has a continuation path. Determinism +1; Durability +1.

3\. Separate the independently embeddable kernel from the full SaaS scaffold. Keep the comprehensive scaffold as a reference implementation, but expose the minimal runtime composition surface and independently versioned contracts needed by an existing application. Coherence +2; Contracts +1.

4\. Reduce orchestration concentration in `LoopManagerImpl`. Move recoverability decisions and lifecycle transition planning into independently testable, preferably pure components. Leave the loop manager responsible for coordinating effects rather than interpreting every rule itself. Coherence +1; Durability +1.

5\. Specify external-effect cancellation and uncertainty precisely. Distinguish cancelling a local await, cancelling a transport operation, and establishing that the external effect did not happen. Require every transport adapter to report which guarantees it actually enforces. Durability +1.

6\. Make the historical projection compatibility contract explicit. Define how historical sessions retain their rendering semantics when prompt templates, model adapters, compaction policies, and schemas change. Prefer stable versioned projections over implicit compatibility. State and context +1.

7\. Publish a stable operational event contract. Define correlation identifiers, event types, ordering guarantees, and reconstruction semantics for run lifecycle, tool attempts, spending, and parked sessions. Streaming and observability +1.

I would prioritize the first three before adding new runtime features.

The most valuable architectural improvement is not another capability. It is making an already ambitious execution contract smaller, more explicit, and more mechanically verifiable.

## Method

Snapshot reviewed: Default branch `main`, accessed October 11, 2026. The current changelog identifies release `v0.16.0`, dated October 11, 2026. I could not independently verify a commit SHA, so this review should be treated as a dated branch snapshot, not a commit-pinned audit.&#x20;

[image](https://www.google.com/s2/favicons?domain=https://raw.githubusercontent.com\&sz=32)

GitHub



Repository material inspected:

- `README.md`, `AGENTS.md`, `CHANGELOG.md`, `pyproject.toml`, and root build/check configuration.
- The complete `agentic_core_spec.md`, including its scope, execution model, recovery, history, models, tools, trust, privacy, budgets, and testing sections.
- All ten architectural lens documents and their index.
- The scaffold's main README, object-model composition root, and selected architecture and checker documentation.
- Substantial implementation paths in `agents`, `agent_sessions`, `steps`, `windows`, `models`, `tools`, and related managers.
- Representative unit tests and conformance-checker documentation.

Depth limitation: I inspected the full normative specification and lens set, plus substantial source and test paths, but did not complete an exhaustive file-by-file audit of the entire application scaffold. I also did not execute the test suite or integration infrastructure. Therefore, I distinguish specified guarantees and visible implementations from independently verified runtime correctness. This is an architecture review, not a proof of implementation conformance.

Adjacent sources, accessed October 11, 2026:

- Pydantic AI + Harness: Current agent concepts, workspaces, deferred tools, durable-execution overview, Step Persistence, and Spend Limits documentation. Exact package version or commit was not independently pinned.&#x20;

  [image](https://www.google.com/s2/favicons?domain=https://pydantic.dev\&sz=32)

  Pydantic Docs

  +3

- LangGraph: Current Python persistence, checkpointing, streaming, and interrupt documentation. Exact package version was not pinned; the reviewed interrupt documentation includes functionality identified as requiring `langgraph>=1.2.12`.&#x20;

  [image](https://www.google.com/s2/favicons?domain=https://docs.langchain.com\&sz=32)

  Docs by LangChain

  +2

- Temporal: Current workflow definition, deterministic replay, task execution, and event-history documentation. No single server or SDK version was assumed.&#x20;

  [image](https://www.google.com/s2/favicons?domain=https://docs.temporal.io\&sz=32)

  Temporal Documentation

  +1



Evidence confidence: High for the conceptual and documented execution contracts; moderate for their concrete implementation across the scaffold; lower for exhaustive crash/recovery correctness and adapter enforcement, because these require execution-based verification. Comparator scores are based principally on documented architecture rather than comprehensive source audits.

Overall judgment, baba: `agentic_core` makes a credible and differentiated architectural claim. Its strongest contribution is bringing durability, authority, historical state, and resource governance into one coherent agent-execution model. Its principal weakness is not the absence of important concepts, but the difficulty of proving all their interactions correct and embedding the resulting kernel without adopting excessive surrounding machinery.

83/100 reflects a strong architecture with meaningful room for simplification and stronger correctness guarantees.
