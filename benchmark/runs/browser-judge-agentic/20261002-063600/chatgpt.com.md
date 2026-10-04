# chatgpt.com

- URL: [redacted]
- Model: —
- Effort: Extra High
- Sent: 2026-10-02T06:36Z (the page shows "Thursday 11:36 PM")
- Finished: — (the page shows "Worked for 4m")
- Status: ok
- Score: 92/100
- Read: the attached agentic_core_spec.md
- Note: The answer is the copy button's Markdown. The page shows no model name; the effort is the label on the page's model picker, which a signed-in view of the share page shows, so it is that picker's state when the page was read. `[redacted]` replaces the name the product addresses the person by (twice) and the name of another repository (once).

## Answer

Score: 92/100

[redacted], I’m scoring this as an **architecture/specification**, not as a mature shipping framework. That distinction matters because `agentic_core` explicitly describes itself as the engine beneath an agent, with durability, bounds, steering, privacy and auditability as engine semantics, while several repository artifacts are still planned. agentic_core_spec agentic_core_spec

My main conclusion is:

> **`agentic_core` is not really another LangChain-style agent framework. It is closer to an agent kernel: a normative execution model for durable, secure, governable agentic computation.**

That distinction is important. Compared with today's major frameworks, the spec is unusually strong exactly where agent systems become difficult in production: crash semantics, side effects, concurrent writers, authorization, prompt injection boundaries, cost enforcement, long-lived sessions, human intervention, isolation, and privacy.

## Evaluation criteria

I used this rubric. Adoption and GitHub popularity are deliberately **not** part of the 92 score.

| Criterion | Weight | `agentic_core` |
|---|---:|---:|
| Core semantic model and conceptual integrity | 10 | 9 |
| Durability, recovery and concurrency correctness | 15 | 14 |
| Tool execution and side-effect semantics | 12 | 11 |
| Security, authorization and trust boundaries | 13 | 12 |
| Context and model abstraction | 10 | 9 |
| Long-running lifecycle, HITL and steering | 10 | 9.5 |
| Budgets and resource governance | 8 | 8 |
| Privacy, auditability and observability | 8 | 7.5 |
| Multi-agent composition | 6 | 5.5 |
| Evolvability, conformance and developer ergonomics | 8 | 6.5 |
| **Total** | **100** | **92** |

The deductions are mostly not because the underlying ideas are weak. They are because several hard contracts are either still implicit, deliberately pushed outside the spec, or not yet proven by the planned conformance implementation.

---

# What is unusually strong

### 1. The model/engine split is exactly right

The core statement, "the model chooses; the engine does," is more important than it looks.

The spec makes the probabilistic model replaceable while assigning durability, approvals, bounds, attribution and recording to deterministic engine code. It also makes virtually every environmental dependency an injected interface. agentic_core_spec agentic_core_spec

That is a much healthier abstraction than allowing the prompt or agent framework to become the de facto control plane.

A lot of agent frameworks began from:

```text
prompt -> LLM -> tools -> LLM -> result
```

and subsequently accumulated persistence, approvals, memory, tracing and limits.

`agentic_core` starts from:

```text
durable deterministic runtime
        |
        +-> probabilistic decision maker
        +-> governed side effects
        +-> immutable history
        +-> policy
        +-> resource accounting
```

That inversion is architecturally significant.

---

### 2. The event model is excellent

The distinction between a request and its response as separate steps is particularly good. A response can arrive later, never arrive, or be truncated. There is no artificial "turn" pretending these are atomic. agentic_core_spec

Likewise:

> written once, referenced everywhere

is a very strong invariant for an agent system. agentic_core_spec

This gives you something close to event sourcing without casually calling every record a domain event. That restraint is good.

The resulting history model is also clean:

* immutable history is truth
* session status is a projection
* context is a projection
* pending inputs are a projection
* cost is derivable
* traces are derivable
* compaction changes what the model sees, not history

The spec says this explicitly. agentic_core_spec

This is one of the strongest parts of the design.

---

### 3. Crash recovery is considerably more rigorous than typical agent frameworks

This may be the best section in the document.

The recovery behavior is specified separately for:

* model request with missing response
* tool awaiting approval
* read-only tool
* idempotent tool
* unsafe tool with unknown outcome

and the idempotency key is grounded in the persisted request step. agentic_core_spec

Then it adds **writer epochs**, so correctness does not depend on an old worker politely noticing that its lease expired. The stale writer simply cannot append or command the transport. agentic_core_spec

That is distributed-systems thinking applied properly to agents.

"Persist before you proceed" plus "recover by effect" plus fencing is much stronger than generic "checkpoint and resume."

---

### 4. Tool effects are first-class architecture, not annotations for the LLM

The split between:

```text
authorization class
effect
```

is excellent.

Authorization asks:

> What power does this operation exercise?

Effect asks:

> What are the replay semantics?

These are completely different questions, and the spec keeps them separate. `read_only`, `idempotent`, and `unsafe` directly drive recovery. agentic_core_spec

Likewise, policy evaluates the tool, authorization class, effect and target rather than trusting the model's characterization of its own action. agentic_core_spec

That is a very mature boundary.

---

### 5. Actor, principal and spender is one of the best ideas in the spec

Most agent architectures blur:

```text
who did it
who authorized it
who paid for it
```

into one "user" or "agent" identity.

Here they are explicitly independent:

* **Actor:** who produced the step
* **Principal:** whose authority permits the operation
* **Spender:** whose budget pays for inference

and the agent is explicitly never a principal. agentic_core_spec

This avoids an enormous family of confused-deputy problems.

The adjacent rule is equally strong:

> only a principal instructs

Tool output, retrieved documents, external events and child reports remain data. agentic_core_spec

And instead of pretending that prompt formatting solves prompt injection, the architecture assumes the model can be convinced and constrains what a compromised model can actually do. agentic_core_spec

That is the correct security posture for agents.

---

### 6. The budget design is materially better than the common industry approach

This is another standout.

Every model call and every spending job goes through one gate **before it begins**, with a reservation for worst-case exposure. The reservation is later settled against actual usage. agentic_core_spec

This matters because most implementations do roughly:

```text
if spent < budget:
    make call
spent += actual_cost
```

which does not enforce a real ceiling.

Pydantic AI's own current Harness documentation explicitly acknowledges this limitation: `SpendLimits` prevents a new request only once a budget is already exhausted; the request crossing the threshold completes, and concurrent runs can race past the limit. [Pydantic](https://pydantic.dev/docs/ai/harness/spend/?utm_source=chatgpt.com)

Claude Agent SDK similarly describes `maxBudgetUsd` as stopping once spend reaches the cap. [Claude Code](https://code.claude.com/docs/en/agent-sdk/agent-loop)

`agentic_core` instead effectively does:

```text
reserve(max_possible_cost)
call()
settle(actual_cost)
```

That is the correct design if "budget" means a budget rather than a runaway-loop warning.

I would keep this as a signature feature.

---

### 7. "Parking" is a genuine domain concept, not an implementation detail

The distinction:

```text
success
failure
error
not now
```

is extremely valuable.

Approval, provider outage, resource shortage, child completion, budget replenishment and human handover are not failures. They are suspended continuations with explicit unlock conditions. agentic_core_spec

That gives long-running agents a much saner lifecycle than retry/error gymnastics.

LangGraph has a powerful related primitive with interrupts: it persists graph state and can wait indefinitely for external input. [Docs by LangChain](https://docs.langchain.com/oss/python/langgraph/interrupts)

But `agentic_core` elevates the broader concept into a unified engine lifecycle rather than just a workflow suspension mechanism.

---

### 8. Context is treated as a deterministic projection of history

This is also better than average.

The spec defines rendering as a deterministic function and orders stable prompt material before volatile material for cache stability. agentic_core_spec

The pinned zone is especially good: objectives and standing instructions come only from principals; summaries containing arbitrary historical data cannot mutate the instruction plane. agentic_core_spec

Compaction then changes the model's view rather than rewriting history. agentic_core_spec

That is much more disciplined than treating message history as both database and prompt.

---

### 9. Sub-agent semantics are restrained

A child:

* gets a clean context
* cannot escalate privileges
* inherits the untrusted mark
* reports to the parent as data
* shares the tree budget
* shares an absolute deadline
* participates in cascading cancellation

agentic_core_spec

That is a very sensible minimal primitive.

It is also interesting that Pydantic AI has independently converged on the clean-context model: current `SubAgents` delegates run with their own message history rather than inheriting the parent's conversation. [Pydantic](https://pydantic.dev/docs/ai/guides/multi-agent-applications/?utm_source=chatgpt.com)

That is a good sign that this abstraction is heading toward an industry consensus.

---

# Where I deduct points

These are the areas I would attack before declaring the architecture settled.

### 1. Batched tool-call concurrency needs stronger semantics

The spec says multiple tool calls from one response can proceed concurrently when allowed, and if one requires approval, other allowed calls can proceed. agentic_core_spec

There is a subtle correctness problem here.

Suppose the model emits:

```text
A: modify config
B: deploy config
C: notify customer
```

and A requires approval while B happens to pass policy.

The engine cannot safely infer that B is independent of A just because they were emitted as sibling tool calls.

Claude Agent SDK takes a conservative approach: read-only operations may execute concurrently, while modifying tools execute sequentially. [Claude Code](https://code.claude.com/docs/en/agent-sdk/agent-loop)

I would formalize one of these:

```text
default:
    concurrent only if all calls are read_only

or:

ToolCall:
    dependencies: [...]
    concurrency_group: ...
```

I strongly prefer conservative semantics in the core, with explicit opt-in parallelism.

This is probably my biggest concrete correctness concern.

---

### 2. The reproducibility boundary is not fully specified

"The same steps render the same bytes" is a great target. agentic_core_spec

But steps alone are insufficient unless the rendering environment is versioned.

The prompt can change because of:

* provider adapter version
* serializer version
* tool schema serialization
* provider normalization behavior
* content block translation
* tokenizer/model metadata
* renderer version
* MCP tool definition version
* capability ordering

Some of these are indirectly pinned by kind/fill/tool-definition versions, but I would make the complete reproducibility tuple explicit:

```text
RenderIdentity {
    agent_kind_version
    fill_set_version
    renderer_version
    provider_adapter_version
    tool_registry_hash
    content_schema_version
}
```

Then `prompt_hash` becomes genuinely diagnostic rather than merely observational.

---

### 3. Side-effect reconciliation should become a contract of its own

The current `unsafe` recovery behavior is conservative and correct:

> outcome unknown, never blindly repeat, let the model verify before retrying.

agentic_core_spec

But I would push this one step further.

A sophisticated effectful tool could expose something like:

```text
execute()
lookup_effect(idempotency_key)
reconcile()
compensate()
```

Not every tool needs every operation.

Pydantic AI now has a surprisingly close adjacent concept: its `StepPersistence` includes an append-only log and a tool-effect ledger, with an explicit `unknown_after_crash` state. But its orchestrator still decides what is safe to do next. [Pydantic](https://pydantic.dev/docs/ai/harness/step-persistence/?utm_source=chatgpt.com)

`agentic_core` can go beyond this by making reconciliation semantics formally part of the tool contract.

---

### 4. The stream/history relationship has one auditability hole

The spec deliberately says stream parts are ephemeral and the complete response becomes one persisted step when the stream finishes. agentic_core_spec

Normally I agree.

But consider:

```text
model streamed 2,000 tokens
user saw 1,700
process hard-crashed
final model_response step was never committed
```

On recovery, you know the request happened, but your durable history may not contain exactly what the human saw.

For an engine advertising strong auditability, that distinction matters.

I would **not** turn every token into a step. But I would define a separate optional durable stream-delivery journal or periodic checkpoint mechanism for environments requiring exact UI auditability.

---

### 5. The sticky `untrusted` mark is safe, but intentionally coarse

Once a session reads untrusted data, the mark stays forever and propagates to descendants. agentic_core_spec

As a default safety mechanism this is excellent.

Operationally, though, it means a six-month agent session that read one webpage on day one is forever subject to the stricter outward-action rule.

I would preserve the sticky mark as the **core conservative answer**, but design room for stronger systems to supply provenance compartments or formally isolated sub-sessions later.

Do not replace it with LLM-based trust classification. The simplicity is a feature.

---

### 6. "A session never ends" is elegant but needs pressure testing

I like the model:

```text
loop ends
session persists
new input starts another loop
```

agentic_core_spec

It solves many conversational-agent problems beautifully.

But "never ends" is a very strong universal statement.

Potential edge cases include:

* tenant migration
* permanently revoked principal
* product object permanently closed
* legal retention boundaries
* identity merges
* incompatible semantic version migrations
* sessions whose purpose itself expires

You already have archive, deletion, cryptographic erasure and purge, so implementation can handle much of this.

I might phrase the invariant more precisely as:

> **A loop has an outcome. A session has a lifecycle, not an outcome.**

That preserves the useful distinction without requiring metaphysical immortality.

---

### 7. Schema evolution deserves to be inside the architecture boundary

The spec explicitly leaves wire formats and schemas out of scope. agentic_core_spec

Wire format can absolutely remain outside.

But persisted `Step` compatibility cannot.

If sessions genuinely survive:

* months
* engine releases
* provider changes
* tool changes
* kind upgrades

then schema evolution is a correctness concern, not implementation detail.

I would want an invariant around:

```text
old persisted history is always readable by a declared migration path
```

plus explicit handling for unknown future step/content types.

---

### 8. The architecture is slightly too dependent on `swe_guidelines`

The spec intentionally says general software architecture belongs to the parent guideline rather than being repeated. agentic_core_spec

Within your ecosystem, this is elegant.

For external adoption, though, `agentic_core` is therefore not quite a closed specification. Understanding important guarantees requires following concepts such as:

* stages
* scopes
* database roles
* work queues
* fences
* transitions
* twins
* ADR conventions

into a second normative document.

I would keep the inheritance, but publish a generated "standalone normative spec" containing the transitive requirements relevant to `agentic_core`.

That would make independent implementations and conformance much easier.

---

# Side-by-side with the closest industry adjacents

As rough adoption signals today, LangGraph has about **42.6k GitHub stars**, Pydantic AI about **20.3k**, and the Claude Agent SDK Python repo about **8.2k**. Stars are crude, but they establish that these are not toy comparisons. [GitHub](https://github.com/langchain-ai/langgraph) [GitHub](https://github.com/pydantic/pydantic-ai) [GitHub](https://github.com/anthropics/claude-agent-sdk-python)

| Area | **agentic_core** | **LangGraph** | **Pydantic AI + Harness** | **Claude Agent SDK** |
|---|---|---|---|---|
| Primary abstraction | Agent kernel / semantic runtime | Stateful graph runtime | Typed agent loop + capabilities | Claude Code agent harness |
| Control model | One generic model-driven loop | Explicit graph + agentic nodes | Agent loop, optional graph/workflow layers | Claude-driven loop |
| Source of truth | Append-only steps | Checkpointed graph state | Messages plus optional append-only StepPersistence | Session transcript |
| Crash semantics | Explicit per effect | Resume/re-execute graph work | Durable execution + effect ledger | Session resume, less formal side-effect protocol |
| Unsafe tool replay | Core semantic distinction | Application responsibility | `unknown_after_crash` exposed | Not a generic effect taxonomy |
| Concurrent writers | Writer-epoch fencing | Runtime/checkpointer semantics | Backend/orchestrator dependent | Process/session model |
| HITL | Native approval + parking + controls | Excellent interrupts | Deferred tools/approval | Mature permissions/approval |
| Authorization | Actor/principal/policy/class/target | Primarily application concern | Application/tool concern | Strong permission subsystem |
| Prompt-injection model | Explicit trust plane | Application concern | Some trust guidance | Permission/isolation oriented |
| Spend governance | Pre-call worst-case reservation | Not core | Multi-window SpendLimits, can overshoot | `maxBudgetUsd`, stops at reached limit |
| Model portability | Designed provider-neutral | High | Very high | Claude-specific |
| Context compaction | Deterministic projection, pinned instructions | Application/state oriented | Flexible processors/capabilities | Very mature automatic compaction |
| Subagents | Child sessions, no escalation, shared tree bounds | Subgraphs/workflows | Clean-context delegation | Mature Claude subagents |
| Privacy | Per-session envelope encryption and crypto-erasure | Application/platform | Application/platform | SDK/environment dependent |
| Conformance philosophy | Planned contract kit | Mature implementation/ecosystem | Strong typing + tests + implementations | Product-quality harness |
| DX today | Spec/scaffold stage | Excellent ecosystem | Excellent Python DX | Excellent if Claude is your model |

LangGraph itself describes its role as a low-level orchestration runtime focused on durable execution, streaming, HITL and persistence. [Docs by LangChain](https://docs.langchain.com/oss/python/langgraph/overview) Its persistence model centers on graph-state checkpoints plus stores. [Docs by LangChain](https://docs.langchain.com/oss/python/langgraph/persistence)

Pydantic AI is now much more serious competition than it would have been even a year earlier. Its Harness has append-only `StepPersistence`, continuable snapshots, a tool-effect ledger, sub-agents, spend controls and other capabilities. [Pydantic](https://pydantic.dev/docs/ai/core-concepts/persistence/?utm_source=chatgpt.com) [Pydantic](https://pydantic.dev/docs/ai/harness/step-persistence/?utm_source=chatgpt.com)

Claude Agent SDK is a highly polished agent harness: the actual Claude Code loop, tools, permissions, sessions, hooks, context compaction and subagents exposed as an SDK. [Claude Platform](https://platform.claude.com/docs/en/agent-sdk/overview) It persists conversation sessions and now supports external session stores for cross-host continuity, but the conceptual persistence unit is still predominantly the conversation transcript rather than an engine-wide event/effect model. [Claude Platform](https://platform.claude.com/docs/en/agent-sdk/sessions)

---

# Where each competitor beats `agentic_core`

This matters as much as where `agentic_core` is stronger.

**LangGraph wins today on orchestration expressivity and ecosystem maturity.** Arbitrary deterministic workflow topology is native to its model. `agentic_core` deliberately says there is one loop. LangGraph can naturally express workflows whose topology itself is business logic. It also has years of production use and deployment/observability tooling. [Docs by LangChain](https://docs.langchain.com/oss/python/langgraph/overview)

**Pydantic AI wins on developer ergonomics and typing.** It is extremely easy to define a typed agent, typed dependencies, typed tools and typed output while remaining model-independent. Its architecture is also rapidly converging toward some of the same production concepts in this spec. [GitHub](https://github.com/pydantic/pydantic-ai)

**Claude Agent SDK wins on harness intelligence and proven coding-agent behavior.** Context management, tool selection, permissions, compaction, subagents and filesystem workflows come from the same machinery that powers Claude Code. The spec would have to implement and tune all of that behavior itself. [Claude Code](https://code.claude.com/docs/en/agent-sdk/agent-loop)

And all three win overwhelmingly today on one dimension:

**they exist as widely used executable software.**

That is intentionally not reflected heavily in the 92 architecture score.

---

# Where `agentic_core` is ahead

If implemented faithfully, I would put five areas clearly ahead of the common abstractions in those systems:

1. **Unified durability semantics.** Persistence is not merely "save state"; request persistence, effect class, replay behavior and writer fencing form one correctness model.
2. **Authority semantics.** Actor, principal and spender are properly separated.
3. **Security model.** It assumes prompt injection eventually succeeds and constrains consequences rather than claiming prompt hygiene solves it.
4. **Economic correctness.** Worst-case reservation before spending is substantially more rigorous than post-response spend accounting.
5. **Privacy semantics.** Per-session envelope encryption, readable shape vs sealed content and crypto-erasure are engine concepts rather than deployment recommendations. agentic_core_spec

That combination is unusual.

---

# LangGraph vs `agentic_core`: the fundamental distinction

I would describe them like this:

```text
LangGraph asks:

"What computation graph should execute,
and how do I checkpoint and resume it?"


agentic_core asks:

"What invariants must hold while an untrusted,
probabilistic decision-maker performs durable,
authorized, potentially expensive side effects?"
```

These are overlapping but different abstractions.

LangGraph's interrupt semantics illustrate the difference nicely: resuming an interrupt restarts the containing node from its beginning, meaning code before the interrupt executes again. [Docs by LangChain](https://docs.langchain.com/oss/python/langgraph/interrupts)

That is perfectly reasonable for a workflow runtime.

`agentic_core` instead attempts to classify each external effect and define exactly what recovery can do with it.

For the domain you appear to be targeting, I prefer the latter as the foundation.

You could theoretically implement a graph orchestrator **above** `agentic_core`; I would be reluctant to implement `agentic_core` semantics above LangGraph because too many correctness properties would then depend on conventions inside graph nodes.

---

# Pydantic AI is the competitor I would watch most closely

Architecturally, this was the surprising result of the comparison.

Current Pydantic AI is not merely:

```text
Pydantic models + LLM calls
```

anymore.

Its Harness now has:

* append-only step persistence
* continuable snapshots
* explicit unresolved tool effects
* sub-agent isolation
* steering
* long-running/deferred tools
* approval primitives
* multi-window spending controls [Pydantic](https://pydantic.dev/docs/ai/harness/step-persistence/?utm_source=chatgpt.com) [Pydantic Docs](https://ai.pydantic.dev/deferred-tools/)


Its docs even explicitly distinguish HITL approval from authorization and warn that client-submitted approval cannot be treated as a security boundary. [Pydantic Docs](https://ai.pydantic.dev/deferred-tools/)

That is sophisticated.

But the philosophy is different.

Pydantic AI largely gives the developer **capabilities that can be composed**.

`agentic_core` says:

> these things are **invariants of being this engine**.

For example, Pydantic's effect ledger exposes `unknown_after_crash` and lets the orchestrator decide. `agentic_core` makes effect classification determine the recovery rule.

That normative stance is the bigger differentiator than any individual feature.

---

# Claude Agent SDK is not actually the closest Anthropic comparison anymore

One current-market wrinkle, [redacted]: in late 2026 **Claude Managed Agents** is actually closer to parts of `agentic_core` than the Agent SDK is.

Managed Agents now has persistent sessions, sandboxes, session budgets and server-side permission policies. [Claude Platform](https://platform.claude.com/docs/en/managed-agents/sessions?utm_source=chatgpt.com) [Claude Platform](https://platform.claude.com/docs/en/managed-agents/permission-policies?utm_source=chatgpt.com)

But it starts to overlap with what your spec explicitly pushes into `[redacted]`, so I would not use it as the primary architectural comparator.

Interesting confirmation of the budget argument: Anthropic documents that a Managed Agents session's request which crosses its spending ceiling is allowed to finish, so final spend may go beyond the nominal cap. [Claude Platform](https://platform.claude.com/docs/en/managed-agents/sessions?utm_source=chatgpt.com)

That makes the pre-reservation approach in `agentic_core` genuinely distinctive even against a 2026 managed agent platform.

---

# What I would change before calling this 1.0

I would make these changes, in this order:

1. **Formalize tool-call concurrency/dependencies.** Side-effecting sibling calls should not race by default.
2. **Define the complete rendering identity.** Pin renderer, adapter and schema versions so deterministic replay means deterministic replay.
3. **Add a first-class effect-reconciliation contract.** `lookup`, `reconcile`, perhaps `compensate`, where supported.
4. **Specify persisted schema evolution.** Old sessions have to survive engine upgrades.
5. **Close the streamed-content audit gap.** Optional durable delivery journal, not token-as-step.
6. **Pressure-test "session never ends."** I would probably rephrase it as "sessions have lifecycle, not outcomes."
7. **Generate a standalone normative spec.** Keep `swe_guidelines` as the source but make conformance possible without recursively reading another architecture constitution.
8. **Build the conformance kit early.** Several beautiful invariants become much more credible once an independent adapter can prove them.

The existing testing direction is very good: scripted provider, replay using prompt hashes, contract cases and deterministic clocks/IDs. agentic_core_spec

---

## Bottom line

**92/100 is high because the architecture is unusually coherent, not because it has a huge feature list.**

The design has a strong center of gravity:

```text
history is truth
model chooses
engine acts
authority is explicit
effects have semantics
state is durable before action
stale writers cannot act
waiting is not failure
money is reserved before spending
untrusted text never becomes authority
secrets are capabilities, not prompt content
```

Those concepts reinforce each other rather than looking like unrelated features.

The most important thing I would **not** do is reposition `agentic_core` as "our answer to LangChain/Pydantic AI." That undersells the architecture.

The sharper category is something like:

> **A durable execution kernel for autonomous agents.**

Pydantic AI can be an excellent API surface. LangGraph can be an excellent workflow runtime. Claude Agent SDK can be an excellent harness.

`agentic_core` is trying to specify **the laws under which the agent is allowed to operate**.

That is a narrower abstraction in one sense, but architecturally a deeper one. 

---

If you want, I can:

- Continue detailed architecture evaluation
- Elaborate on comparison criteria
- Provide recommendations for next steps
