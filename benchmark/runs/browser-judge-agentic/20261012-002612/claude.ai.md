# claude.ai

- URL: [redacted]
- Model: Opus 5.5
- Effort: High
- Sent: 2026-10-12T00:28:13Z
- Finished: 2026-10-12T00:46:22Z (the poll whose page held "Claude finished the response")
- Status: ok
- Score: 77/100
- Read: 4e90bff (committed 2026-10-11 17:09 PDT), read on 2026-10-11
- Polls: 11
- Note: the composer's permission chip read Auto; the page's accessibility tree held a "Disable computer actions on Claude.ai" element the page did not draw, and no line saying computer actions are available, or naming a device, showed before the send, right after it, or at any poll; the prompt went as one pasted card ("Pasted text.txt"); the run's tab group closed at about 00:31 while the answer was being written, and the conversation was reopened from its URL; the answer opens with a one-line preamble before its Score line; its Method says it saw the index of benchmark/runs/ before scoring and did not use those verdicts

## Answer

I'll read the `agentic_core` repository in full, then research the closest adjacents and score everything under one frozen rubric.

Score: 77/100

## Classification

- **What it is:** a specification (`agentic_core_spec.md`) for one tool-call loop in which the model decides and the engine acts. The loop is made durable, bounded, steerable, private and auditable. The repository also holds a Python/Postgres reference implementation (`scaffold/acme_root/`), lenses, a checker and skills that hold code to the spec.
- **Abstraction level:** below agent design and above infrastructure. The kernel of a session-based agent runtime: steps, loops, sessions, context rendering, policy, budgets and parking.
- **What it owns:**
  - the record (append-only steps, the writer epoch, recovery by tool effect);
  - context rendering and compaction;
  - model roles and switches;
  - the tool contract, policy and approvals;
  - the budget gate and parking;
  - trust tiers and attribution;
  - per-session sealing of content;
  - sub-agent trees.
- **What it delegates:**
  - general software design, to the pinned `swe_guidelines` v0.59.0;
  - fleet concerns (runners, placement, billing), to a platform;
  - prompts, tools and agent kinds, to the product;
  - liveness and leases, to the adopter;
  - default values and the threat model, to each system.
- **What it is not:**
  - a graph or workflow orchestration framework;
  - a prompt or agent-design toolkit;
  - a library you install (you render a scaffold);
  - a fleet scheduler;
  - an evaluation harness.
- **Category: a governed, durable agent-runtime kernel.** It fits better than "agent framework" because it gives you no composition DSL and refuses to borrow one ("The Engine and the Brain"). It fits better than "durable-execution engine" because its durability is specific to a model loop: recovery depends on each tool's effect, persistence is ordered around the prompt cache, and budget holds are taken before each call.

## Adjacents

- **OpenHands Software Agent SDK (v1.54.0).** It has the closest whole-engine scope: an event-sourced conversation log, swappable workspaces, a confirmation policy, a condenser, and pause and resume. It overlaps Steps, Durable by Default, The Runtime, Approvals, Compaction and Steering.
- **LangGraph (1.2.14, with LangChain v1 1.4.4 middleware).** It is the reference point for checkpointed durability and interrupt-and-resume. It overlaps Durable by Default, History, Parking and Approvals (through `interrupt()`), and Streams.
- **Pydantic AI (main @ `69ea1e5`, 2026-10-10; latest tag v2.55.0).** It is the reference point for a typed provider boundary, deferred approvals, usage limits, and durability delegated to Temporal, DBOS or Prefect. It overlaps Models, The Tool Contract, Approvals, Bounds and Budgets, and Testing and Conformance.

## Rubric

Frozen before any scoring.

| # | Criterion | Measures | 10/10 looks like | Weight |
|---|---|---|---|---|
| C1 | Conceptual model & contract clarity | How few, orthogonal and precise the core abstractions are; whether types, states and outcomes are explicit | A small set of nouns with one meaning each, explicit state machines and outcome sets, typed contracts, nothing left to guess | 10 |
| C2 | Durability, recovery & state model | Where truth lives, persistence ordering, crash recovery, idempotency, fencing | One append-only source of truth, persist before acting, recovery rules per effect, stale writers fenced, no lost inputs; specified and tested | 14 |
| C3 | Context & model management | How context is built (determinism, cache stability, compaction) and how models are abstracted, switched and failed over | Deterministic, cache-aware rendering; compaction that never loses the record; provider-neutral content; explicit, recorded switches; classified provider errors | 10 |
| C4 | Tool contract & execution runtime | How tools are declared (effect, timeout, mode) and where and how they execute | Typed tools that declare effect, class and timeout; one execution boundary for every side effect; isolation chosen up front and never weakened; long jobs that release the runtime | 12 |
| C5 | Governance, trust & data security | Policy, approvals, prompt-injection containment, secrets, attribution, privacy | Policy decides by declared class and target, never by what the model claims; approvals bound to exact calls; trust tiers plus capability limits; secrets never in the record; encryption and erasure per unit | 12 |
| C6 | Control flow, steering & composition | Termination, interrupts, mid-run inputs, suspension, sub-agents | An explicit outcome set; first-class suspension with named reasons; durable mid-run steering; bounded sub-agent trees that cannot escalate | 10 |
| C7 | Bounds, budgets & cost accounting | Limits on spend, time and steps; where they are enforced; usage records | Worst case reserved before the call across shared scopes; fails closed; usage recorded per call; guards distinguished from bounds | 8 |
| C8 | Streaming, observability & auditability | Typed live events, tracing, audit, debuggability | Typed streams that never block the loop; full audit from the record; standard tracing conventions; cost and trace as queries | 8 |
| C9 | Testability, determinism & conformance | Test doubles, replay, deterministic seams, contract suites | A scripted model twin, replay checked by prompt hash, a fake clock and ids, an importable conformance kit for every interface | 8 |
| C10 | Extensibility, interop & operational cost | Injection points, escape hatches, standard protocols, cost to adopt and run | Every dependency injected, with null objects; MCP and OTel; embeddable at several scales; low adoption and operating cost; a clear upgrade path | 8 |
| | | | **Total** | **100** |

## Scores

| Criterion | Weight | agentic_core | OpenHands SDK | LangGraph | Pydantic AI |
|---|---|---|---|---|---|
| C1 Conceptual model & contracts | 10 | 8 | 7 | 7 | 8 |
| C2 Durability & recovery | 14 | 9 | 6 | 6 | 6* |
| C3 Context & models | 10 | 8 | 6* | 4* | 7* |
| C4 Tools & runtime | 12 | 8 | 7* | 5* | 6* |
| C5 Governance & security | 12 | 8 | 5* | 3* | 4 |
| C6 Control flow & composition | 10 | 8 | 6 | 7 | 7 |
| C7 Bounds & budgets | 8 | 8 | 5 | 3 | 5 |
| C8 Streaming & observability | 8 | 6 | 6* | 5* | 7 |
| C9 Testability & conformance | 8 | 7 | 6* | 6* | 7 |
| C10 Extensibility & operations | 8 | 5 | 8* | 8 | 9 |
| **Weighted result /100** | 100 | **77** | **62** | **54** | **65** |

\* Medium confidence: the evidence is documentation plus partial reading of the source.

The scopes differ:
- LangGraph is a general graph runtime.
- Pydantic AI is an in-process library that makes durability a pluggable capability.
- OpenHands is closest to agentic_core's scope.
- Each criterion is scored on what the system specifies for that criterion, not on how much it covers overall.

## agentic_core, criterion by criterion

**C1 — 8.**
- Strengths:
  - The Core lists sixteen invariants, each linked to its section.
  - "Concepts at a Glance" defines every noun once.
  - Every set is closed: the six step families (Step Types), the five outcomes (Loops and Their Outcomes), the eight park reasons (Parking), the provider error kinds (Provider Errors) and the six tool failure classes.
  - The `core`/`default`/`optional` tags (How to Read This) say which rules are invariants.
- What held it back:
  - The prose is a dense idiolect, and its meaning depends on an external guideline cited by section ([g-lro], [g-workq]).
  - The spec contradicts itself on usage: a "child" in What a Step Carries, but "shape" in Content and Shape.
  - The session state diagram in A Session Never Ends has `running → pending` on a yield. The code never writes that transition; `steps`/`agent_sessions` leave the status at `running`.

**C2 — 9.**
- This is the strongest part of the design.
- Persist before acting, and the recovery table keyed on each tool's effect (Durable by Default).
- Each run takes a writer epoch on the session's cursor row, before it reads the history.
- A gapless `seq` (What a Step Carries).
- The history is the only truth (History).
- The code matches:
  - `steps/storage/impl/postgres.py` increments the epoch and makes appends conditional on it; it raises `StaleWriter`, and transports refuse commands from a stale epoch (`infra/transports/records.py`).
  - The steps migration runs `REVOKE UPDATE, DELETE`.
  - `tools/impl/manager.py:recover` re-runs repeatable calls under the request id and asks the transport about unsafe ones.
  - There are contract tests in `om/tests/contracts/step_storage.py`.
- What held it back:
  - The budget hold is authorized before its request is written (`agents/impl/loop.py:_call`), which ADR 1009 admits.
  - A lost run's pending approval is answered `interrupted` instead of re-parked, unlike the spec's table.
  - The rule that provider-side tools recover as `unsafe` is not implemented.
  - Every turn reloads and rescans the whole history (`_history`, `exchanges`), so the cost of a turn grows without bound in a session that never ends.

**C3 — 8.**
- Strengths:
  - `render(...)` is pure and layered from the most stable part to the least (Rendering).
  - The keyed prompt hash is recorded and replay is tested (`test_windows.py`).
  - Only principals feed the pinned zone (The Pinned Zone).
  - A summary references the range it replaces (Compaction; ADR 1008), and an overflow is retried once.
  - Model roles, fills and asks are explicit, every switch writes a `switched` step (`models/impl/manager.py:switch_fill`), and stop reasons and error kinds are tables in both adapters.
- What held it back:
  - Spec vs code: the spec says `model_unavailable` "re-resolves the model role and switches". The code only takes a declared fallback.
  - Unreachable switches: the retired, upgrade and policy switches are declared, but nothing produces them.
  - Unproven boundary: no test runs the loop through the OpenAI adapter, although The Provider Boundary names a second adapter as the proof.
  - Summarizer path: the summarizer never falls back and does not read the outage mark.

**C4 — 8.**
- Strengths:
  - The Tool Contract declares class, effect, timeout, interruptible, mode and preflight, and `ToolSpec` with `extra="forbid"` inputs implements them (`tools/types/tool.py`).
  - One boundary for every command or file touch: `ToolRuntime` and the execution transport (The Runtime).
  - Isolation is "refused, never weakened", and tests show a container spec is never swapped for a directory.
  - A timeout ends the whole process tree (`transports/processes.py`), and job mode releases the runtime (Long-Running Jobs).
- What held it back:
  - Tool calls from one response run one after another (`loop.py:_tool_turn`). The spec's Execution section, lens TOL-08 and ADR 1023 all say they run concurrently.
  - Every provider refuses `ALLOWLIST` egress, because no proxy exists.
  - `refuse_unsafe` leaves the container as the only runtime deployable in the cloud.
  - A base archive read back from its bucket is not checked against its hash, unlike a snapshot.

**C5 — 8.**
- Under this rubric, this is the most complete governance design of the systems compared.
- Policy decides on tool, class, effect and target, never on the model's text (Policy). It is layered, with the platform's ceilings on top.
- Approvals are bound to the call's HMAC and expire (`rules.decides`).
- The untrusted mark and the rule of two (Bound What a Convinced Model Can Do) are sticky and inherited.
- Secrets never enter a step: injection runs from a stripped environment, redaction holds back a stream to catch split values, and the audit records names only.
- Every session has its own envelope key, sealing is a decorator over step storage, and revoking the key erases the content (A Key per Session; `StepStorageSealedImpl`).
- Actor, principal and spender are separate answers (Who Is Who).
- What held it back:
  - Several approval clauses are unbuilt: class grants, later-chosen targets, a different approver and two-person approval. A requester can approve their own call.
  - A real broker is unbuilt: only `BrokerNullImpl` and `BrokerTwinImpl` exist, so injection is the only working path.
  - A revoked key's wrapped copies survive in database backups while the KMS key lives.
  - `execute` calls in an open-egress container are not stamped outward.
  - Any API-key client is treated as a principal.

**C6 — 8.**
- Strengths:
  - Five outcomes, and parks that name their reason, unlock and retry time and hold no runtime (Parking).
  - An inbox that is a projection of input steps, not a second queue (The Inbox).
  - Controls that travel out of band (Controls).
  - Sub-agent trees bounded by height, count, money and one shared deadline, with no escalation and the strictest ancestor policy deciding (Sub-Agents; `agents/impl/manager.py:spawn`, `tests/contracts/sub_agents.py`).
- What held it back:
  - Controls are polled from the database every 500 ms and are not seen during a model stream.
  - "An urgent message interrupts an interruptible tool" is not implemented.
  - The tree's concurrency bound is stored but not enforced.

**C7 — 8.**
- Strengths:
  - One gate before every model call and every spending job, holding the worst case, with settle and release rules (One Gate, Before the Call).
  - A refusal lists every breach and fails closed (Breaches and Failing Closed).
  - Usage records hold no content and survive purges (Usage and Cost).
  - Guards, bounds and yields are distinguished, and the step guard cannot be disabled (Time, Steps, and Streaks; `agent_sessions/limits.py`).
- What held it back:
  - The hold counts prompt bytes, including the raw bytes of attachments, as tokens (`windows/rules.py`). It over-reserves several-fold, which risks spurious refusals.
  - Side model roles have no path through the engine, so the adopter must wire the gate by hand.
  - `thinking_outside` and `provider_tools` exposure are declared but never filled.

**C8 — 6.**
- The spec is sound:
  - typed stream parts that never block the loop, and a whole response that is a collector over its stream (Streams);
  - trace as a query, with OTel GenAI spans that carry shape only (Tracing);
  - spend as a reading (Spend Is a Reading).
- The implementation lags, and the score holds it to what is built:
  - The engine creates no GenAI spans.
  - Artifact stream parts do not exist.
  - Only null and in-memory stream sinks exist, so live viewing is unwired.

**C9 — 7.**
- Strengths:
  - A scripted provider raises every error kind.
  - Re-rendering reproduces the prompt hash.
  - Contract modules exist (`om/tests/contracts/`), the clock can be injected, and there are about 2.6k tests.
- What held it back:
  - Testing and Conformance promises "a deterministic id source", but `new_id()` calls `uuid7()` globally.
  - The conformance kit is not importable: it is reachable only by rendering the scaffold.
  - There is no shared provider-adapter contract.
  - Three error kinds are never driven through the loop.
  - Replay over a real stored history is declared only.

**C10 — 5.**
- Strengths:
  - Every dependency is an interface, with quiet or loud null objects and a refusal at boot of a quiet gate or ledger outside `local` (Null Objects; `om/root.py`).
  - The MCP client pins definitions by hash.
  - Upgrades follow a merge path (Adopting the Guideline).
- What held it back:
  - Adoption means rendering a whole multi-tenant SaaS base.
    - About 80k lines of Python, and the engine is roughly 35–40% of the code.
    - Four Postgres roles, plus Valkey, SQS, S3, WorkOS and AWS Terraform.
    - Python 3.14 is required.
  - No package and no versioned API surface.
  - The session runner has no deployment.
  - The engine is tied to a second, external guideline.
  - The spec says a laptop and a unit test are valid hosts. In practice, a plain-process embedding still pulls in tenancy.

## Where it differs

- **Stronger: per-effect recovery under a fence.** The idempotency key is the request step's id; the transport is asked for the outcome of an unsafe call; the writer epoch fences stale runs. This buys at-most-once side effects across crashes. None of the adjacents has it: OpenHands reports an interrupted tool as failed, LangGraph re-runs the node, and Pydantic AI is at-least-once.
- **Stronger: a budget hold reserved before the call.** It stops overshoot and races between concurrent sessions. All three adjacents check limits after the call.
- **Stronger: per-session envelope keys, with erasure by revoking the key.** Content can be erased while cost, audit and trace stay intact. No adjacent separates content from shape.
- **Stronger: policy keyed on declared class and target, with an untrusted mark and the rule of two.** OpenHands' risk analysis starts from the model's own label; LangGraph and Pydantic AI leave policy to the application.
- **Weaker: tool calls run one after another in practice.** OpenHands locks tools per declared resource, and LangGraph runs a step's tasks in parallel, so their wall-clock time on fan-out turns is better.
- **Weaker: observability.** Pydantic AI ships OTel GenAI instrumentation and typed event streams that work today. agentic_core specifies both and builds neither, so it cannot be debugged live as built.
- **Weaker: adoption.** Pydantic AI and LangGraph install as a package and run on nothing. agentic_core costs a whole platform to try.
- **Broader: tenancy, attribution, approver roles, workspace snapshots, VM machines and shared budget scopes are owned by the engine.** Every adjacent leaves these to the application or a hosted product.
- **Narrower: agentic_core offers no graph or workflow composition.** LangGraph owns arbitrary control flow; agentic_core has one loop with kinds and sub-agents.
- **Narrower: no evaluation tooling.** Pydantic AI ships evals; agentic_core leaves judging a kind to a platform.
- **Different: an append-only step log vs checkpoint snapshots.** agentic_core's log wins on audit, erasure and replay. LangGraph's snapshots win on time travel, branching and cheap resume of arbitrary state.
- **Different: a durability design built in and owned vs delegated to Temporal.** Owning it gives agent-specific semantics: parking, effects and the prompt cache. Delegating to Temporal (Pydantic AI) wins when a team already runs Temporal and wants general workflows next to its agents.
- **Different: a spec, lenses and a rendered scaffold vs a library.** agentic_core's approach wins for a platform that must hold dozens of invariants over years. A library wins for products that want to change agent design quickly.

## What I would change

1. **Split an importable engine package from the SaaS base.** It would hold the interfaces, the loop, and the memory and null impls, with the conformance kit as a published test package. Tenancy would become one adopter. Moves C10 by about +2 and C9 by about +1.
2. **Make code and spec agree on concurrent tool calls.** Either run calls under a `TaskGroup` per tool, or rewrite Execution, ADR 1023 and TOL-08 to say "in order". Moves C4 by about +0.5 and C1 by a little.
3. **Mark Streams' artifact parts and the OTel spans in Tracing as `optional` until they exist, or build them and a real stream carrier.** Moves C8 by +1 to +2.
4. **Cut the approval clauses that are not built from Approvals:** class grants, later-chosen targets and two-person approval. Or implement at least "approver ≠ requester". Moves C5 by about +0.5.
5. **Replace bytes-as-tokens in the hold with the provider's token count or a calibrated bound per media type.** Route side model roles through an engine call path that always passes the gate. Moves C7 by about +1.
6. **Load each turn from the latest summary onward, and index open calls, so a turn's cost stays bounded in a session that never ends.** Moves C2 by about +0.5.
7. **Fix the spec's inconsistencies.** Usage as a child vs as shape; `running → pending` on a yield; `model_unavailable` re-resolving vs falling back; the re-park of an approval on recovery. Record each departure as an ADR. Moves C1 by about +0.5.
8. **Add a deterministic id seam and a shared provider-adapter contract, and run the loop suite over the OpenAI adapter.** Moves C9 by about +0.5 and C3 by about +0.5.
9. **Narrow "the first answer is a broker" in Secrets Never Enter a Step to match what ships, or build an egress proxy, which would also make `ALLOWLIST` real.** Moves C5 and C4 by about +0.5.
10. **Move `agentic-benchmark-browser` and its runs out of the engine repository.** They are off-mission for an engine.

## Method

- **The repository:** `baristaze/agentic_core` at `4e90bff` (committed 2026-10-11 17:09 PDT), read on 2026-10-11. It holds 1,609 files.
- **Read in full by me:** the spec, `README.md`, `AGENTS.md`, `CHANGELOG.md`, `lenses/README.md`, the ADR index, and the benchmark README and prompt.
- **Read by three parallel auditors** (summarized above), each against the spec, with key claims spot-checked by me:
  - loop and durability (`om/` agents, steps, windows, agent_sessions, storage, migrations, session runner);
  - tools, runtime, trust and privacy (`om/tools`, attribution, privacy, `infra/` workspaces, transports and keys, `integrations/mcp`);
  - models, budgets, streams, testing and tooling (providers, budgets, contracts, `checkers/`, a sample of lenses, skills, CI).
  - The claims I checked myself: sequential `_tool_turn`, no engine spans, the broker impls, and no two-person approval.
- **Not run:** the test suite (no environment). "Tested" means a test with matching assertions exists.
- **Not read whole:** the lenses (sampled), the guideline-inherited SaaS code, and the TypeScript and Terraform.
- **Prior scores:** `benchmark/runs/` holds earlier chat products' scores of this repository. I saw its index before scoring and did not use those verdicts.
- **OpenHands SDK:** `OpenHands/software-agent-sdk` main @ `c047136` (v1.54.0, 2026-10-11) source, plus docs.openhands.dev/sdk/arch/overview, read the same day. Read through a delegated agent.
  - Thin evidence: C3, C4, C5, C8, C9 and C10 (medium).
- **LangGraph:** `langchain-ai/langgraph` main @ `799c2f04` (langgraph 1.2.14, checkpoint 4.3.0, checkpoint-conformance 0.0.2) and `langchain` @ `b060a746` (1.4.4), plus docs.langchain.com pages on persistence, interrupts, the functional API, observability and double texting, read 2026-10-11. Read through a delegated agent.
  - Thin evidence: C3, C4, C5, C8 and C9.
  - The open-source library was scored, not the hosted Platform.
- **Pydantic AI:** `pydantic/pydantic-ai` main @ `69ea1e5` (2026-10-10; tag v2.55.0) source, plus the docs at pydantic.dev/docs/ai, read 2026-10-11. Read through a delegated agent.
  - Thin evidence: C2, C3 and C4.
  - C2 depends on which durable backend is configured.
- **Scores on agentic_core that rest on thinner evidence:** C8 and C10, which lean on the auditor's count of lines and infrastructure, and C5's backup-revocation finding, which comes from reading the code, not from a test.
