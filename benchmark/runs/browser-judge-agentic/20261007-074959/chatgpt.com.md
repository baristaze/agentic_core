# chatgpt.com

- URL: [redacted]
- Model: Latest
- Effort: High
- Sent: 2026-10-07T07:51:15Z
- Finished: 2026-10-07T08:08:50Z ("Worked for 14m 39s" above the answer, seen at poll 7)
- Status: ok
- Score: 89/100
- Read: main as served on October 7, 2026
- Polls: 7
- Note: none

## Answer

Score: 89/100

## Classification

[redacted], **what it is:** an invariant-heavy execution engine for long-running, model-directed agents, where the model chooses actions but the engine owns execution, durability, authority, budgets, recovery, and the durable record. [GitHub](https://github.com/baristaze/agentic_core/blob/main/agentic_core_spec.md)

**Abstraction level:** below product-specific agents, prompts, and tools, but above model providers, storage, execution transports, key services, and other infrastructure adapters. [GitHub](https://github.com/baristaze/agentic_core/blob/main/agentic_core_spec.md)

**Architectural role:** the trustworthy control kernel around an LLM-driven loop, particularly when that loop can survive minutes, months, crashes, approvals, provider outages, changing models, and human steering.

**Core execution model:** `Session -> Loops -> Runs -> Steps`; history is append-only truth, context is a deterministic projection, model/tool requests are persisted before execution, and a restarted run reconciles unfinished work according to the effect semantics of that work. [GitHub](https://github.com/baristaze/agentic_core/blob/main/agentic_core_spec.md)

**Primary abstractions:** sessions, immutable steps, windows, model roles/fills, tool registry and policy, budgets/holds, agent kinds, principals/actors/spenders, parks, workspace/transport, storage modes and session keys. The object model makes these namespaces explicit. [GitHub](https://github.com/baristaze/agentic_core/blob/main/agentic_core_spec.md)

**Extension points:** nearly every environmental dependency is injected: model providers, storage, key service, pricing, budget ledger, workspace, transport, outage signal, stream sink, result gate, tool registry, clock and related services. The scaffold reflects that separation rather than merely describing it. [GitHub](https://github.com/baristaze/agentic_core/blob/main/agentic_core_spec.md)

**It owns:** the model/tool loop, its durable history, context derivation, crash semantics, tool-effect semantics, approvals, attribution, bounds, budget gating, subagent semantics, steering, privacy modes and the guarantees tying those pieces together.

**It delegates:** product prompts/tools/kinds to the application, fleet placement/runners/billing/integrations to the platform layer, and generic software architecture conventions to `swe_guidelines`. [GitHub](https://github.com/baristaze/agentic_core/blob/main/agentic_core_spec.md)

**It is not:** a DAG/workflow authoring language, a fleet scheduler, a model-provider SDK, a prompt framework, an agent-product framework, or a generic durable-computation platform.

**Category:** **durable agent execution kernel**. That is substantially more precise than "agent framework": the central contribution is not making it easy to define agents, but making model-directed execution safe and recoverable under real-world failure.

## Adjacents

**Pydantic AI:** closest on the agent-runtime side: typed model/tool interaction, provider abstraction, streaming, deferred tools, HITL, limits, and pluggable durable-execution backends. It overlaps the upper half of `agentic_core`, while deliberately delegating durability to engines such as Temporal, DBOS, Restate and others. [Pydantic Docs](https://ai.pydantic.dev/durable_execution/)

**LangGraph:** closest on explicit agent orchestration and persistent state: graphs, checkpointed execution, interrupts, resumability, subgraphs and long-running workflows. It overlaps `agentic_core`'s state/coordination layer but uses checkpointed graph execution rather than an agent-specific event ledger. [Docs by LangChain](https://docs.langchain.com/oss/python/langgraph/persistence)

**Temporal:** closest on the hardest part of `agentic_core`, durability itself. Its durable event history, deterministic replay, activities, messages and child workflows provide the strongest adjacent architecture for crash recovery and long-running execution, while leaving model/tool/agent semantics entirely above it. [Temporal Documentation](https://docs.temporal.io/encyclopedia/event-history)

I would not pick OpenAI Agents SDK or Claude Agent SDK over these three. They overlap the agent-loop surface, but they illuminate less of what is architecturally distinctive about `agentic_core`: durable state, recovery, side-effect semantics and execution authority.

## Rubric

The following rubric was frozen before scoring any system.

| Criterion | What it measures | What 10/10 looks like | Weight |
|---|---|---|---:|
| Conceptual model & coherence | Orthogonality of abstractions, ownership, lifecycle and separation of concerns | Every runtime concern has one authoritative abstraction; state and lifecycle are unambiguous; little duplicated machinery | 12 |
| Durability, recovery & side-effect correctness | Crash/retry semantics, persist-before-effect, writer fencing and uncertain external outcomes | Every crash point has defined semantics; stale executors cannot act; unsafe effects are never blindly repeated | 16 |
| State, context & replayability | Canonical truth, context derivation, compaction/checkpoints, replay and version stability | One canonical record; deterministic views; replay is reproducible; compaction never destroys truth | 12 |
| Tool/action, policy & failure semantics | Action contract, effects, authorization, approvals, targets, retries, jobs and failures | One contract governs schema through policy, execution, retry and recovery | 11 |
| Model/provider abstraction & control | Provider independence, capabilities, roles, switching, fallback and leakage containment | Callers express intent/capability rather than vendors; adapters are replaceable and switching is explicit/auditable | 8 |
| Security, authority & privacy boundaries | Principal authority, untrusted input, least privilege, secrets, isolation and deletion | Authority is non-textual and fail-closed; secrets stay outside the agent boundary; privacy guarantees are end-to-end | 12 |
| Long-running coordination, steering & concurrency | Parking, resume, external events, children, cancellation, ordering and deadlines | Arbitrarily long waits consume no execution slot; concurrent and external events have deterministic semantics | 9 |
| Observability, attribution & resource accounting | Causal traceability, actor/payer attribution, usage and spend | Every important decision/effect/cost is queryable and attributable without requiring sensitive content | 8 |
| Extensibility, interoperability & escape hatches | Replaceable components and ability to embed or integrate without breaking guarantees | Stable seams permit alternative providers, stores, runtimes and orchestrators without bypassing invariants | 6 |
| Contract clarity, testability & operational ergonomics | Precision, executable tests, conformance, misuse resistance and operational burden | Contracts are executable, deterministic and difficult to misconfigure; semantic regressions are caught automatically | 6 |
| **Total** |  |  | **100** |

## Scores

| Criterion | Weight | agentic_core | Pydantic AI | LangGraph | Temporal |
|---|---:|---:|---:|---:|---:|
| Conceptual model & coherence | 12 | **9.0** | 8.6 | 8.7 | **9.2** |
| Durability, recovery & side-effect correctness | 16 | 9.3 | 8.2 | 7.8 | **9.5** |
| State, context & replayability | 12 | **9.0** | 8.0 | 8.8 | **9.0** |
| Tool/action, policy & failure semantics | 11 | **9.2** | 8.4 | 7.2 | 7.5 |
| Model/provider abstraction & control | 8 | 8.7 | **9.4** | 7.8 | 5.0 |
| Security, authority & privacy boundaries | 12 | **8.8** | 7.0 | 6.2 | 7.2 |
| Long-running coordination, steering & concurrency | 9 | 8.5 | 8.6 | 9.2 | **9.6** |
| Observability, attribution & resource accounting | 8 | **9.1** | 8.5 | 8.1 | 8.2 |
| Extensibility, interoperability & escape hatches | 6 | 8.3 | **9.5** | 9.3 | 9.0 |
| Contract clarity, testability & operational ergonomics | 6 | 8.2 | **9.1** | 8.6 | 8.2 |
| **Weighted result** | **100** | **89/100** | **84/100** | **81/100** | **83/100** |

These are **not global framework rankings**. They answer a narrower question: *how good is each architecture when evaluated as infrastructure for a durable agent execution kernel?* Temporal's 5.0 on model abstraction, for example, is not an architectural defect in Temporal. Temporal intentionally does not own that layer.

Pydantic AI is particularly strong where `agentic_core` is relatively lower-level: model/provider abstractions, composable agent capabilities and integration ergonomics. Its durable-execution design wraps model requests and tool calls using an external durable engine, and its deferred-tool model covers approvals and external long-running work well. But its documentation explicitly distinguishes approval from a real authorization boundary. [Pydantic Docs](https://ai.pydantic.dev/durable_execution/)

LangGraph has arguably the strongest programmable orchestration model here after Temporal: explicit graphs, subgraphs, interrupts and checkpoints give the application considerable control. The trade-off is visible in its failure model: resuming an interrupt re-runs the containing node, so preceding side effects must be idempotent or factored into separate nodes. That is a materially weaker invariant than `agentic_core`'s per-effect recovery protocol. [Docs by LangChain](https://docs.langchain.com/oss/javascript/langgraph/thinking-in-langgraph)

Temporal remains the reference architecture for generic durable execution. Commands become durable events, workflow code is replayed to reconstruct state, external non-deterministic work is separated into Activities, and external messages advance workflow state. That machinery is more general and more rigorous than `agentic_core`'s custom durability layer, but Temporal deliberately has no opinion about principals versus actors, prompt context, model roles, tool-effect classes or AI-specific budget semantics. [Temporal Documentation](https://docs.temporal.io/encyclopedia/event-history)

## agentic_core, criterion by criterion

**1. Conceptual model & coherence: 9.0/10.**  
The architecture in `agentic_core_spec.md`, especially **The Core**, **Scope**, **The Engine and the Brain**, **Steps**, and **The Object Model**, is unusually disciplined. "Model chooses; engine does" is not just a slogan: authority, durability, policy, budgeting and recording are explicitly removed from model behavior. `Step` as the atomic record plus `Session/Loop/Run` as execution boundaries gives most concepts one owner. The scaffold reinforces the model with dependency injection rather than collapsing infrastructure into the loop implementation. [GitHub](https://github.com/baristaze/agentic_core/blob/main/agentic_core_spec.md) The remaining point is mostly lost to lifecycle edges: "a session never ends" is conceptually useful but leaves archival, retention termination and extremely long-lived schema evolution less crisp than the immediate execution model.

**2. Durability, recovery & side-effect correctness: 9.3/10.**  
This is the strongest part of the design. The specification's **persist before you proceed**, effect-sensitive recovery and stale-writer rule are first-class invariants rather than implementation advice. The scaffold provides real evidence: `StepStoragePostgresImpl.begin_run()` advances an epoch used for fencing, while `LoopManagerImpl._recover_calls()` resolves abandoned calls before allowing new execution. `ToolsManager.recover()` re-executes only repeatable effects; otherwise it queries the transport for the previous outcome rather than blindly doing the action again. [GitHub](https://github.com/baristaze/agentic_core/blob/main/agentic_core_spec.md) That is materially stronger than the usual "please make your tools idempotent" contract. What prevents 10 is the absence of a compact formal crash-interleaving model covering every cut point and concurrency race. The semantics are strong, but still spread among prose, lenses and code instead of being reducible to one executable transition table.

**3. State, context & replayability: 9.0/10.**  
`agentic_core_spec.md` **History** establishes an append-only step history as the sole source of truth, with projections reconstructible from it and model-context compaction unable to alter historical truth. Deterministic rendering and recorded prompt hashes make context an auditable derived artifact rather than mutable conversation state. [GitHub](https://github.com/baristaze/agentic_core/blob/main/agentic_core_spec.md) This is architecturally cleaner than checkpoint-only designs for audit-sensitive agents. The limitation is long-horizon evolution: a session intended to survive months or years needs extremely explicit compatibility rules for renderer versions, kind versions, tool-schema versions and historical model adapters, plus some equivalent of history rollover/archive semantics. Those pieces exist partially through pinned versions, but not yet as one complete long-lived-history protocol.

**4. Tool/action, policy & failure semantics: 9.2/10.**  
`agentic_core_spec.md` **Tools**, **Approvals**, and **The Runtime**, backed by `lenses/tools.md` and `tools/impl/manager.py`, treat a tool call as much more than a function invocation. Class controls authority, target controls policy scope, effect controls retry/recovery, failures have explicit classes, approval binds by default to the exact call, and long work can become a durable job. Importantly, the model cannot self-assert these attributes. [GitHub](https://github.com/baristaze/agentic_core/blob/main/agentic_core_spec.md) This is one of the clearest architectural advantages over most agent frameworks. The principal weakness is metadata trust: somebody still has to classify a tool's effect and authority class correctly. The architecture needs stronger conformance expectations around incorrect or malicious declarations, especially for external/MCP tools and unsafe effects.

**5. Model/provider abstraction & control: 8.7/10.**  
The **Models** design is strong: call sites name `ModelRole`, an injected resolver maps the role to a fill, and fills describe provider/model/capabilities/limits/eligibility. Kinds bind roles rather than hard-coded vendors, and provider concerns remain below the loop. [GitHub](https://raw.githubusercontent.com/baristaze/agentic_core/main/lenses/models.md) That is a better architecture than scattering concrete model names throughout agent definitions. Pydantic AI is currently more complete in this dimension: its `Model`, provider/profile layer, runtime model selection, fallback mechanisms and developer-facing adapter surface are richer and more concretely documented. [Pydantic Docs](https://ai.pydantic.dev/models/) `agentic_core` would move toward 9.5 with more precise cross-provider semantic conformance around tool-call ordering, stop reasons, partial usage, capability negotiation and fallback determinism.

**6. Security, authority & privacy boundaries: 8.8/10.**  
This is another unusually strong section. `actor`, `principal` and `spender` are separate identities; the engine never mints authority; the agent itself is not a principal; only principal-originated material can instruct; untrustedness is sticky; policy is external to model text; isolation fails closed; and session content can be envelope-encrypted under a per-session key whose destruction leaves audit shape while eliminating readable content. [GitHub](https://raw.githubusercontent.com/baristaze/agentic_core/main/lenses/trust.md) The scaffold's routed private-history construction demonstrates that storage privacy modes are implementation concepts too, not only prose. [GitHub](https://raw.githubusercontent.com/baristaze/agentic_core/main/scaffold/acme_root/om/src/acme/om/root.py) The main missing architectural artifact is the thing the spec explicitly declines to be: a threat model. Per-person deletion inside a shared session is also deliberately unresolved, and any fallback that lets credentials enter an agent process necessarily weakens the otherwise excellent secrets boundary.

**7. Long-running coordination, steering & concurrency: 8.5/10.**  
Parking is correctly separated from loop termination; guards park while actual inability to progress yields a terminal loop outcome; inputs and controls can revive long-lived sessions; children are sessions rather than magical nested model calls; spawn itself is a gated tool; and shared tree bounds constrain runaway fan-out. [GitHub](https://github.com/baristaze/agentic_core/blob/main/agentic_core_spec.md) The important weakness is not lack of functionality but precision under concurrency. Several tool calls may proceed concurrently, human controls may arrive, children may complete and jobs may wake simultaneously, yet the exact deterministic arbitration and persisted ordering rules are less explicit than the otherwise excellent sequential Step model. Temporal and LangGraph have a clearer generalized concurrency/control-flow story, although neither gives the same agent-specific safety semantics.

**8. Observability, attribution & resource accounting: 9.1/10.**  
The architecture makes cost and causality properties of the durable record rather than ephemeral telemetry. Usage names loop, role, kind and tree; budgets are checked before model/spending work; holds reserve worst-case cost and are settled against reported usage; actor/principal/spender separation answers who acted, who authorized and who pays. [GitHub](https://raw.githubusercontent.com/baristaze/agentic_core/main/lenses/bounds.md) This is significantly more rigorous than retrospective token counters. One unavoidable hole is partial streaming: stream parts deliberately are not durable Steps, so the final record cannot reconstruct every token/timing event preceding a broken stream. By comparison, Pydantic AI explicitly warns that usage after cancelled streams can be partial/provider-dependent and unsuitable for cost-critical accounting, which illustrates why `agentic_core`'s pre-call accounting model is valuable. [Pydantic Docs](https://ai.pydantic.dev/agents/)

**9. Extensibility, interoperability & escape hatches: 8.3/10.**  
The engine is highly decomposed internally. `LoopManagerImpl` consumes interfaces for steps, sessions, kinds, attribution, models, windows, tools, policy/budget gate, providers, outage signal and stream sink; root wiring composes privacy implementations behind one interface. [GitHub](https://raw.githubusercontent.com/baristaze/agentic_core/main/scaffold/acme_root/om/src/acme/om/agents/impl/loop.py) Null implementations and conformance contracts further reduce conditional infrastructure logic. [GitHub](https://github.com/baristaze/agentic_core/blob/main/agentic_core_spec.md) The relative weakness is *external* composability. Architecturally it is a complete kernel with strong opinions about storage roles, history and orchestration. There is not yet an equally crisp story for "use all agent semantics, but place durability underneath Temporal/DBOS/Restate" or "embed only these three invariants into an existing runtime." Pydantic AI's durable-engine builder is a particularly good counterexample here. [Pydantic Docs](https://ai.pydantic.dev/durable_execution/)

**10. Contract clarity, testability & operational ergonomics: 8.2/10.**  
The `core/default/optional/style` distinction is excellent. Lenses turn prose into reviewable invariant groups; the conformance design calls for contract cases for every major implementation interface plus fake clocks, deterministic identifiers and scripted model providers. [GitHub](https://github.com/baristaze/agentic_core/blob/main/agentic_core_spec.md) There is also substantial executable scaffold behind the prose. The score stays at 8.2 because the architecture contract is distributed across three layers: spec, lens and scaffold. Some operationally important details, including wire-level contracts and threat assumptions, are deliberately excluded. A reader sometimes has to triangulate which document is normative and which scaffold behavior merely realizes one legal implementation. That is fixable mainly by clarification, not more machinery.

## Where it differs

**Stronger:** versus Pydantic AI and LangGraph, `agentic_core` makes effect classification, persist-before-effect, stale-writer fencing, unknown-effect recovery, principal authority and cost preflight part of one engine contract; this sharply reduces the number of safety properties an application author must remember manually. LangGraph, for example, explicitly requires the developer to account for node re-execution around interrupts. [GitHub](https://raw.githubusercontent.com/baristaze/agentic_core/main/scaffold/acme_root/om/src/acme/om/tools/impl/manager.py)

**Stronger:** its agent-specific durable history is semantically richer than a generic checkpoint. "Why was this action allowed?", "who authorized it?", "who paid?", "what model role requested it?", and "could this effect legally be replayed?" are native questions about the record rather than conventions layered on afterward.

**Weaker:** Temporal has the superior generic durability substrate. Its deterministic workflow replay, durable Command/Event protocol and service/worker execution model handle arbitrary long-running applications, not just agent loops. `agentic_core` therefore owns significant distributed-systems machinery that Temporal already isolates as a dedicated runtime. [Temporal Documentation](https://docs.temporal.io/encyclopedia/event-history)

**Weaker:** Pydantic AI currently has the better model/provider abstraction surface and developer escape hatches. It can combine its agent model with multiple durable execution engines rather than making the agent runtime itself the durability implementation. [Pydantic Docs](https://ai.pydantic.dev/durable_execution/)

**Broader:** relative to Pydantic AI and LangGraph, `agentic_core` owns more security and governance semantics: actor/principal/spender, instruction provenance, policy ceilings, effect-aware recovery, budgets and cryptographic session deletion.

**Narrower:** relative to LangGraph and Temporal, it intentionally does not provide arbitrary workflow topology. The dominant orchestration primitive remains a model-driven loop plus child sessions. LangGraph gives applications arbitrary explicit graph structure; Temporal gives them general durable programs.

**Different:** the three durability models are fundamentally different. `agentic_core` persists agent-domain events and reconstructs projections; LangGraph checkpoints graph state and restarts execution at node boundaries; Temporal persists workflow command/event history and replays deterministic code. `agentic_core` wins when agent semantics themselves are the integrity boundary, LangGraph when graph topology is the application model, and Temporal when the requirement is generic durable computation. [GitHub](https://github.com/baristaze/agentic_core/blob/main/agentic_core_spec.md)

## What I would change

1. **Formalize concurrency and recovery as one transition/interleaving table**, covering model completion, multiple tool completions, user input, control, child completion, job wakeups, crashes and stale writers. This could move **Durability 9.3 -> ~9.6** and **Coordination 8.5 -> ~9.0** without adding a new abstraction.

2. **Add an executable failure-cut conformance matrix for tools/transports**: crash before send, after send, after remote commit, before acknowledgement, after acknowledgement and before local persistence, for every effect category. This could move **Durability +0.3** and **Tool/action +0.2**.

3. **Specify long-lived history evolution explicitly**: renderer version, kind version, fill-set version, tool-contract version, migration compatibility and history archival/rollover while preserving the "history is truth" invariant. This could move **State/replay 9.0 -> ~9.4**.

4. **Add a small architectural threat model rather than more security features.** State attacker capabilities and trust assumptions for tool metadata, untrusted content, approvals, workspace isolation, key compromise, connected clients and external transports. This could move **Security 8.8 -> ~9.3**.

5. **Generate an invariant traceability index** mapping each Core invariant to specification section, lens ID, scaffold symbol and conformance test. That would remove the largest documentation weakness and move **Contract clarity 8.2 -> ~8.7**.

6. **Define model-adapter semantic conformance**, not just interface conformance: tool ordering, stop reasons, partial responses, usage completeness, retry ownership, provider capability differences, fallback eligibility and deterministic switch records. This could move **Model/provider 8.7 -> ~9.2**.

7. **Clarify the embedding boundary:** explicitly document whether durability may be delegated to Temporal/DBOS/Restate while retaining `agentic_core`'s step/policy/budget semantics. A clean separation between "agent semantics kernel" and "durability implementation" could move **Extensibility 8.3 -> ~8.8** and would remove substantial future operational burden.

8. **Specify stream reconnect/audit semantics**, perhaps persisting only shape-level stream metadata rather than token content. This is lower priority, but would improve **Observability** and **Coordination** by roughly 0.2 each.

The first five matter far more than adding more features. I would resist broadening the framework. Most of the remaining score is available through **formalization and removal of ambiguity**, not scope expansion.

## Method

**agentic_core:** evaluated `main` as served on **October 7, 2026**. I read the full 1,695-line `agentic_core_spec.md`, the repository-level architecture/documentation, all ten architectural lens groups (`steps`, `windows`, `models`, `tools`, `live`, `trust`, `agents`, `bounds`, `economy`, `privacy`), and traced their important guarantees into the scaffold, including loop execution/recovery, PostgreSQL step fencing, tool recovery/policy, root composition/privacy and the contract/conformance structure. Generated/derived material was checked as corroboration rather than counted as independent architectural evidence. [GitHub](https://github.com/baristaze/agentic_core/blob/main/agentic_core_spec.md)

**Evidence standard for agentic_core:** I gave full credit only where a rule is actually specified. I gave greater confidence where the scaffold implements it. For example, stale-writer fencing and effect-aware recovery have implementation evidence; some long-horizon compatibility and concurrency semantics remain primarily architectural prose.

**Pydantic AI:** current primary documentation read on **October 7, 2026**, including Agents, Models and Providers, Durable Execution overview, Temporal durable execution, Deferred Tools/HITL, streaming/cancellation and related runtime contracts. The strongest evidence is on model abstraction and composable durable execution; security/privacy scoring has somewhat lower confidence because many authorization/isolation choices intentionally belong to the application or external runtime. [Pydantic Docs](https://ai.pydantic.dev/durable_execution/)

**LangGraph:** current primary Python/JS LangGraph documentation read on **October 7, 2026**, especially Persistence, Interrupts and architecture guidance around node boundaries, checkpoints and recovery. Scores around persistence and orchestration are high confidence; security/privacy and agent-specific resource accounting are lower confidence because those concerns largely sit outside LangGraph core. [Docs by LangChain](https://docs.langchain.com/oss/python/langgraph/persistence)

**Temporal:** current primary Temporal documentation read on **October 7, 2026**, covering Event History, Workflow Tasks/replay, deterministic versus non-deterministic execution, Signals/Queries/Updates and Child Workflows. Durability and coordination scores are high confidence. The low model/provider score is a deliberate scope mismatch, not thin evidence and not a criticism of Temporal as a generic workflow system. [Temporal Documentation](https://docs.temporal.io/encyclopedia/event-history)

**Bottom line:** `agentic_core` is architecturally more interesting than a new agent framework. Its distinctive contribution is the attempt to define **agent execution as a trustworthy systems problem**, with durability, authority, effects, spending, privacy and history sharing one coherent semantic model. At **89/100**, the main gap is no longer conceptual design. It is turning several very strong prose-level invariants into formally exhaustive, executable contracts, particularly around concurrent interleavings, external-effect uncertainty and years-long history evolution.
