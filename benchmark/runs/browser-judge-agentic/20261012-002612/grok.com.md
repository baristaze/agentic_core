# grok.com

- URL: [redacted]
- Model: Expert
- Effort: none
- Sent: 2026-10-12T00:29:25Z
- Finished: 2026-10-12T00:35:04Z (the poll that saw Enter voice mode beside the composer, after a poll that showed the whole answer)
- Status: ok
- Score: 86/100
- Read: main, commit 4e90bff8, 2026-10-12
- Polls: 3
- Note: the run's tab group closed at about 00:31 while the answer was being written, and the conversation was reopened from its URL, where the answer was whole

## Answer

Score: 86/100

## Classification
- What it is: the durable execution engine under one long-running agent’s tool-call loop, with the model as decision-maker.
- Abstraction level: session/loop runtime core (not application, not fleet).
- Owns: the loop itself, step history as source of truth, durability/recovery semantics, tool policy by class/effect, budget gate, parking, session-key sealing, model-role resolution, sub-agent trees with shared bounds.
- Delegates: general software design (the guideline), model providers and adapters, workspace/isolation implementations, fleet placement/billing/runners (the platform), and product-specific prompts/tools/kinds.
- Is not: a full agent framework, a graph DSL, a general workflow engine, or a model SDK.
- Category: durable agent session engine (more precise than “agent framework” because it owns the loop, record, and safety properties while remaining domain- and provider-neutral).

## Adjacents
- LangGraph (docs/checkpointing ~2026): overlaps on stateful multi-step execution, checkpoints for durability/recovery, and human-in-the-loop pauses.
- Temporal (AI/durable-execution docs ~2026): overlaps on durable recovery via event history, long-running waits, and idempotent activities; agent decisions sit outside the workflow.
- Claude Agent SDK (docs ~2026): overlaps on the production agent loop, tools, sessions, sub-agents, permissions, and context compaction that power a real long-running coding agent.

## Rubric
| Criterion | What it measures | 10/10 looks like | Weight |
|-----------|------------------|------------------|--------|
| Conceptual model and abstraction quality | Clarity and precision of the core ideas (loop, step, session, role, park, etc.) | Invariants are few, named, linked to sections, and sufficient to derive the rest | 10 |
| Separation of concerns and ownership | Explicit ownership vs. deliberate delegation; pure core with injected boundaries | Engine is pure; every external concern is an interface; layers (guideline/engine/platform/product) are crisp | 10 |
| Durability, recovery, and failure semantics | Persist-before-act, recovery rules, writer identity, parking vs. failure | Recovery is effect-driven and fully specified; stale writers are refused; parks are first-class and distinct from outcomes | 12 |
| State/history as source of truth and context management | Append-only record, deterministic rendering, windows, compaction | History is the only source of truth; rendering is pure and cache-friendly; projections are rebuildable | 10 |
| Tool abstraction, policy, and effect-based recovery | Contract, classes, effects, policy independence from model claims | Policy keys only on class/effect/target; recovery follows declared effect; approvals park cleanly | 10 |
| Model role independence and provider boundary | Roles vs. models, neutral content, versioned fills | Call sites name roles; adapters are thin; switches are explicit and versioned | 8 |
| Bounds, budgets, and cost accounting | Gate placement, scopes, holds, tree sharing, fail-closed | One gate before every spending action; worst-case hold; breaches reported and fail closed | 8 |
| Steering, live control, and streaming model | Inbox durability, out-of-band controls, non-blocking streams | Inputs durable on arrival; controls never queue; emission never blocks the loop | 7 |
| Privacy, isolation, and trust boundaries | Secrets, sealing, isolation refusal, actor/principal, untrusted mark | Secrets never enter steps; isolation is refused not weakened; content sealed per session; only principals instruct | 12 |
| Testability, determinism, and extensibility | Pure core, deterministic render, null objects, conformance hooks | Engine runnable with fakes; render is deterministic; extension points are named and versioned | 8 |
| Architectural coherence and operational simplicity | Internal consistency and avoidable complexity | Design hangs together around the invariants; operational concerns are pushed outward without loss of guarantees | 5 |

## Scores
| Criterion | Weight | agentic_core | LangGraph | Temporal | Claude Agent SDK |
|-----------|--------|--------------|-----------|----------|------------------|
| Conceptual model and abstraction quality | 10 | 9 | 7 | 6 | 7 |
| Separation of concerns and ownership | 10 | 9 | 6 | 7 | 6 |
| Durability, recovery, and failure semantics | 12 | 10 | 8 | 9 | 5 |
| State/history as source of truth and context management | 10 | 9 | 8 | 7 | 7 |
| Tool abstraction, policy, and effect-based recovery | 10 | 9 | 5 | 4 | 7 |
| Model role independence and provider boundary | 8 | 8 | 6 | 5 | 6 |
| Bounds, budgets, and cost accounting | 8 | 8 | 4 | 5 | 5 |
| Steering, live control, and streaming model | 7 | 8 | 6 | 6 | 8 |
| Privacy, isolation, and trust boundaries | 12 | 10 | 3 | 4 | 6 |
| Testability, determinism, and extensibility | 8 | 8 | 7 | 8 | 5 |
| Architectural coherence and operational simplicity | 5 | 8 | 7 | 8 | 6 |
| **Weighted result** | **100** | **86** | **62** | **63** | **61** |

## agentic_core, criterion by criterion
**Conceptual model and abstraction quality (9):** The Core section lists a tight set of invariants (model chooses/engine does, one event one step, history is source of truth, parking is not an outcome, roles not models, etc.). Concepts at a Glance and the object-model namespaces make the vocabulary precise. Minor deduction for residual aspirational language in some optional sections and for the still-preliminary status of the spec.

**Separation of concerns and ownership (9):** Scope and the layered diagram (guideline → engine → platform → product) are explicit. The engine is described as pure with every dependency injected (The Engine and the Brain). The scaffold is domain-free and the lenses push checkable detail outward. The main limit is that some operational details (leases, work-queue fencing) are correctly delegated but still require the adopter to understand the interaction.

**Durability, recovery, and failure semantics (10):** Durable by Default gives a concrete recovery table keyed on tool effect; writer epochs on the cursor row refuse stale writers; persist-before-proceed is required for both model and tool requests. Parking is first-class and distinct from the five loop outcomes. This is unusually complete for an agent engine.

**State/history as source of truth and context management (9):** History is append-only and the sole source of truth; rendering is a pure function of versioned kind, pinned zone, window, and inputs, with explicit cache breakpoints. Compaction and large-result artifacts are specified. Small ambiguity remains around exact window-edge and summary-pinning edge cases under concurrent steering.

**Tool abstraction, policy, and effect-based recovery (9):** The Tool Contract requires class and effect; Policy decides solely on those plus target attributes, never on model claims; Approvals park the loop. MCP tools take the same contract. Recovery follows the effect. The design is strong; the remaining gap is that preflight and interruptible semantics are lightly specified.

**Model role independence and provider boundary (8):** Roles and Fills, versioned fill sets, and a provider-neutral content model are clear. Thinking is acknowledged as non-replayable across providers. The boundary is good but not fully closed (adapters and twin testing are sketched rather than exhaustively specified).

**Bounds, budgets, and cost accounting (8):** One Gate Before the Call, scopes, windows, worst-case holds, and tree-shared budgets are well drawn. Fail-closed on breach is explicit. Pricing and ledger details are delegated, which is correct, but leaves the exact hold-settlement protocol a bit thin.

**Steering, live control, and streaming model (8):** Inbox inputs are durable on arrival and delivered at the next model call; controls are out-of-band. Streams are typed parts that never block the loop; artifacts stream with hash. Solid; the carrier failure model is correctly delegated but underspecified for multi-viewer trees.

**Privacy, isolation, and trust boundaries (10):** Secrets Never Enter a Step, isolation is refused never weakened, content sealed per session key (with three deletes), actor/principal/spender distinct, only a principal instructs, and the untrusted mark is sticky. This is among the strongest parts of the design.

**Testability, determinism, and extensibility (8):** Pure engine, deterministic render, null objects, and twins are named. Lenses and skills provide review hooks. The scaffold exists, but the degree to which the engine itself is already unit-testable with fakes is only partially demonstrated.

**Architectural coherence and operational simplicity (8):** The design coheres tightly around the listed invariants. Complexity (epochs, park reasons, tree bounds) is justified by the guarantees. Operational machinery is pushed to the guideline and platform, which keeps the engine focused.

## Where it differs
- Stronger: effect-driven recovery + writer epochs (Durable by Default) give precise, non-reentrant semantics that neither LangGraph’s checkpoints nor Temporal’s replay nor the Claude SDK’s session model specify at the same granularity; session-key sealing and “isolation refused, never weakened” are absent or much weaker in the adjacents.
- Weaker: control-flow expressiveness is a single loop plus sub-agent trees rather than LangGraph’s arbitrary graphs or Temporal’s full workflow DSL; the Claude Agent SDK ships a battle-tested practical loop and tool set today, while agentic_core remains primarily specified.
- Broader: owns the full durability, policy, budget-gate, parking, and privacy contract for one session; LangGraph and the Claude SDK leave more of this to the application or to an opaque runtime.
- Narrower: deliberately does not own graph-shaped orchestration, general workflow, fleet placement, or product tools/prompts.
- Simply different: history-as-append-only-steps with deterministic rendering trades LangGraph’s flexible state reducers and Temporal’s pure event-history replay for stronger prompt-cache stability and effect-based recovery; the pure injected-engine style wins for testability and multi-host portability, while the Claude SDK’s subprocess model wins for rapid “give the agent a computer” deployment.

## What I would change
- Clarify and freeze the exact recovery protocol for truncated tool-use blocks, provider-side tools, and concurrent tool calls under a lost writer epoch (Durability + Tool abstraction; +1 each).
- Specify the budget-gate hold/settle protocol and multi-scope composition more tightly, including the interaction with tree-shared budgets (Bounds; +1).
- Make the context-window and summary-pinning rules fully deterministic under concurrent inbox delivery and mid-turn compaction (State/history; +0.5–1).
- Add an explicit conformance suite sketch (or lens) for the recovery table and writer-epoch refusal (Testability; +0.5).
- Document the stream-carrier failure and multi-viewer tree model more precisely so adopters know what is guaranteed versus delegated (Steering/streaming; +0.5).

## Method
- Read the full agentic_core_spec.md (main, commit 4e90bff8, 2026-10-12, ~1950 lines) plus README, lenses/steps.md sample, scaffold/README, and object-model structure; deep on the spec, shallower on scaffold implementation.
- LangGraph: official durable-execution and checkpoint docs (~2026).
- Temporal: AI/durable-execution docs and related blog posts (~2026).
- Claude Agent SDK: official overview and related docs (~2026).
- Scores for Claude Agent SDK durability and Temporal tool-policy rest on thinner public documentation of those exact concerns; all other scores rest on the materials above.
