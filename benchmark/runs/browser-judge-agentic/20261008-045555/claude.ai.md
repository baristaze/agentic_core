# claude.ai

- URL: [redacted]
- Model: Opus 5.5
- Effort: High
- Sent: 2026-10-08T04:57:29Z
- Finished: 2026-10-08T05:21:42Z (the moment the page text first read "Claude finished the response", at poll 24)
- Status: ok
- Score: 76/100
- Read: commit 3922410e ("Release 0.9.0"), 2026-10-07 10:19 PDT
- Polls: 24
- Note: the composer's permission chip read Auto, its tooltip "Claude runs on its own and pauses to ask if anything looks unsafe."; the page's accessibility tree also held a "Manual" button that the page does not draw; the composer held no draft on arrival, so nothing was cleared; the prompt went as one pasted card ("Pasted text.txt"); right after the send a banner under the conversation read "Permission mode is set to Auto. Claude runs on its own and pauses if anything looks unsafe. This includes using your connectors and Claude in Chrome, unless you turn them off."; it describes the mode, names no device, and does not say computer actions are available, so the session went on; no line naming a device or saying computer actions are available showed before the send, right after it, at any poll, or while the answer was read; no dialog asked to fetch a page or use a tool; the page's step lines named file reads, commands, task updates, and sub-agents; the answer opens with a one-line preamble before its Score line; its Method says benchmark/runs/* holds earlier judge reports for this same prompt and that it deliberately did not open their results, and it names no earlier run and no earlier score

## Answer

I'll clone and read the full `agentic_core` repository first, then pick adjacents, freeze a rubric, and score.

Score: 76/100

## Classification

- **What it is:** a specification of the engine under one long-running agent, plus a reference core that implements it. The engine is a tool-call loop with a model as its decision-maker, and the spec fixes its invariants: the loop is durable, bounded, steerable, private and auditable.
- **Abstraction level:** below an agent framework's authoring API and above a durable-execution engine. Its unit is the *session*: an append-only history of *steps*, cut into *loops*, each ending in one of five outcomes or a *park*.
- **What it owns:**
  - the loop's ordering and crash recovery;
  - the step record;
  - context rendering and compaction;
  - the tool contract and its policy;
  - the budget gate;
  - who-is-who attribution (actor, principal, spender);
  - sub-agent trees;
  - parking;
  - per-session encryption at rest;
  - null objects.
- **What it delegates:**
  - general software design, to the external `swe_guidelines`;
  - the fleet (runners, placement, billing, integrations), to a platform;
  - prompts, tools and agent kinds, to the product;
  - every dependency (storage, keys, ledger, pricing, providers, workspace, transport, clock, sink), to injected interfaces.
- **What it is not:**
  - not a library you import and compose: you render the scaffold under your own name and own the copy;
  - not a graph or workflow DSL: one loop serves every agent, and a kind is a profile over it;
  - not a general durable-execution engine: its durability is specialised to model calls and tool effects;
  - not a hosted service.
- **Category:** **a durable, governed agent-loop kernel, specified first.**
  - "Agent framework" suggests a composition API, which it deliberately lacks (spec, The Engine and the Brain: the loop is "never borrowed from a framework").
  - "Durable-execution engine" misses that its recovery is keyed on *tool effect* and model-call semantics, not on replaying generic code.
  - "Kernel" fits: a small set of invariants and system calls (gate, policy, transport, append), with everything else injected.

## Adjacents

- **LangGraph 1.2.14:**
  - *Why chosen:* its checkpointed super-steps, `interrupt()`/`Command(resume)` and human-in-the-loop middleware are the closest OSS match to durability and parking.
  - *Overlap with agentic_core:* Durable by Default, History, Parking, Steering, Streams.
- **Pydantic AI 2.54.0:**
  - *Why chosen:* it is a model-agnostic typed loop with a pre-call request limit, deferred (approval) tools, durable execution delegated to Temporal/DBOS, and OTel GenAI spans.
  - *Overlap with agentic_core:* Models, Tools, Bounds and Budgets, Testing.
- **Claude Agent SDK, Python 0.2.164 / TypeScript 0.3.293:**
  - *Why chosen:* it is a production agent loop with layered permissions, a Bash sandbox, automatic compaction, subagents with fresh context, and a cost cap.
  - *Overlap with agentic_core:* Tools/Policy, The Runtime, Context, Sub-Agents, Bounds.
- **Not chosen:** Temporal. Its overlap is only the durability layer, and LangGraph and Pydantic AI's Temporal integration already carry that comparison.

## Rubric

Frozen before scoring. **Scoring interpretation:** a design counts fully when it is specified concretely *and* realised in the system's code or documentation. A design that is stated but unrealised, or contradicted by the code, counts at most half. Scope a criterion does not measure is neither rewarded nor penalised.

| # | Criterion | Measures | 10/10 looks like | Weight |
|---|---|---|---|---|
| 1 | Conceptual model & coherence | Clarity, minimality and consistency of the core nouns and invariants; contract clarity | Few orthogonal concepts, each with one meaning; invariants stated and enforced; no drift between documentation and code | 10 |
| 2 | Durability & failure semantics | Persistence ordering, crash recovery, concurrent-writer safety, error taxonomy, suspend/resume | Every side effect recovers by a declared rule; stale writers are fenced; every error kind has a defined engine action; suspension holds nothing | 14 |
| 3 | Context & history | Source of truth, windowing, compaction, cache stability, what survives compaction | An immutable history; deterministic, cache-ordered rendering; compaction that changes what is read, never what is kept; instruction provenance survives | 9 |
| 4 | Tool contract & action governance | Tool abstraction, side-effect declaration, policy, approvals | A typed contract with effect and authority declared; policy decided by declared attributes, never by model text; approvals bound to the exact call, with expiry and separation of duties | 12 |
| 5 | Security, trust & isolation | Sandboxing, secrets, prompt-injection containment, privacy at rest | Isolation that refuses rather than degrades; secrets never in context; a trust-tiered renderer; blast-radius rules; cryptographic erasure | 10 |
| 6 | Bounds & cost control | Budgets, pre-call enforcement, usage accounting, time/step limits, tree sharing | A worst-case hold before every spend; correct settlement; multi-scope budgets; a backstop no configuration can disable | 9 |
| 7 | Model/provider independence | Call-site neutrality, provider boundary, fallback/switch semantics | Call sites name tasks, not models; one content model; explicit, recorded switches; proven with two or more adapters | 7 |
| 8 | Composition & extensibility | Multi-agent model, extension points, escape hatches | Sub-agents and custom control flow from the same primitives; every dependency replaceable; no fork needed to extend | 8 |
| 9 | Streaming, steering & observability | Typed live output, mid-run input and control, tracing | Typed, non-blocking streams; durable mid-run input; out-of-band controls; standard traces with content off | 8 |
| 10 | Testability & conformance | Test doubles, determinism, replay, contract kits | An offline twin model; deterministic clock and ids; replay that reproduces prompts; an importable conformance kit for every interface | 7 |
| 11 | Adoption & operational cost | What it takes to adopt, run and upgrade | Small surface, incremental adoption, minimal infrastructure, painless upgrades | 6 |
| | | | **Total** | **100** |

## Scores

| Criterion | Weight | agentic_core | LangGraph | Pydantic AI | Claude Agent SDK |
|---|---|---|---|---|---|
| 1 Conceptual model & coherence | 10 | 8 | 7 | 8 | 6 |
| 2 Durability & failure semantics | 14 | 9 | 7 | 6 | 4 |
| 3 Context & history | 9 | 8 | 6 | 6 | 7 |
| 4 Tool contract & governance | 12 | 8 | 5 | 6 | 7 |
| 5 Security, trust & isolation | 10 | 8 | 4 | 4 | 6 |
| 6 Bounds & cost control | 9 | 8 | 3 | 6 | 5 |
| 7 Model/provider independence | 7 | 7 | 7 | 9 | 2 |
| 8 Composition & extensibility | 8 | 7 | 9 | 8 | 6 |
| 9 Streaming, steering & observability | 8 | 6 | 8 | 8 | 7 |
| 10 Testability & conformance | 7 | 7 | 7 | 8 | 3 |
| 11 Adoption & operational cost | 6 | 5 | 7 | 8 | 6 |
| **Weighted result /100** | 100 | **76** | **63** | **68** | **54** |

Rounding is half-up; LangGraph's raw result is 62.5. Scopes differ: LangGraph and Pydantic AI are general libraries, and the Claude Agent SDK is a single-vendor productised loop. Each is scored only on what it specifies for each criterion.

## agentic_core, criterion by criterion

**1. Conceptual model & coherence: 8.**

*Strengths:*
- The nouns are few and sharp (spec, Concepts at a Glance):
  - a step is one event, and a request and its response are two steps ("One Event, One Step");
  - every grouping references steps and never copies them;
  - five outcomes end a loop, and a park is none of them ("Loops and Their Outcomes");
  - guard, bound and yield are distinct limits ("Time, Steps, and Streaks");
  - actor, principal and spender are three answers ("Who Is Who").
- The `core` / `default` / `optional` tags ("How to Read This") make the substitutability of every rule explicit, which is rare.
- The code mirrors the namespaces of "The Object Model" (`om/src/acme/om/{steps,windows,models,tools,budgets,agents,attribution,privacy}`).

*What held it back:*
- The spec leans on an external guideline for its meaning (`TenantContext`, Database Roles, the Work Queue, ADR 0039), so the design cannot be read whole from this repository.
- The vocabulary is large, about 30 terms.
- `agents/impl/loop.py` is 1,453 lines, with 14 injected collaborators and invariants guarded by `assert`.
- There is real spec/code drift. On recovery, a call awaiting approval is answered `interrupted` (`loop.py:859-863`), where the spec's table says "Parks again". The nudge limit is configured in two places (`agents/rules.py:44`, `agent_sessions/limits.py:209`).

**2. Durability & failure semantics: 9.**

*Specified ("Durable by Default"):*
- persist before acting, for every request and response;
- a recovery table keyed on the tool's declared effect, which never repeats an `unsafe` call and records `interrupted` so the model verifies before retrying;
- a writer epoch taken on the cursor row *before* reading the history.

*Realised:*
- The epoch upsert and conditional appends are in `steps/storage/impl/postgres.py:104-169`; a lost claim raises `StaleWriter`.
- The transport refuses stale commands in `infra/transports/records.py:82-97`.
- Recovery by effect is in `tools/impl/manager.py:375-401`, and truncated tool uses are never executed.
- 18 step-storage contract tests cover the stale epoch and gapless `seq`.
- "Provider Errors" maps every error kind to an engine action (`model_providers/types.py:43-83`, `loop.py:619-682`).
- "Parking" gives each park a reason, an unlock and a retry time, and a park holds no runtime.

*What held it back:*
- The approval-row drift above. It is arguably the safer behaviour, but it is undocumented.
- `model_unavailable` does not re-resolve as the spec says; it only tries declared fallbacks.
- The agents-only note on provider-side tools recovering like `unsafe` is unimplemented.

**3. Context & history: 8.**

*Specified and realised:*
- "History" makes the step log the truth and everything else a projection.
- "Rendering" is a pure function, laid out from the most stable layer to the least, with a keyed prompt hash on every `model_request` (`windows/rules.py:772-819`, `windows/impl/manager.py:315-335`).
- "The Pinned Zone" is fed only by principal messages, verbatim and then as a cited digest (`windows/rules.py:403-441`). This is a thoughtful defence against summaries laundering injected text into instructions.
- "Compaction" (elide, summarise, retry once on overflow) is implemented and passes the budget gate.

*What held it back:*
- The step for a large result tells the model "a read tool pages through it" (`windows/rules.py:597`), but no native tool exposes `get_artifact`, so the promised retrieval path is dead.
- The objective is never revised.
- "Compact first on a switch" is only implicit.
- Non-waking inputs do not fold into a digest.

**4. Tool contract & governance: 8.**

*Specified and realised:*
- "The Tool Contract" declares class, effect, mode, interruptibility and preflight, all realised in `tools/types/tool.py:253-277`, with unknown input fields refused.
- "Policy" keys only on tool, class, effect and target. `PolicyCall` carries no model text (`tools/types/policy.py:226-233`).
- Policy is layered: kind defaults, then the tenant, capped by platform ceilings. An unmatched call goes to a person.
- A child's calls are decided under every ancestor's layer, and the strictest decision holds.
- Approvals bind to an HMAC of the input and expire.
- MCP tools are pinned by definition hash, and the adopter assigns their class and effect (`tools/mcp.py:29-41`).

*What held it back:*
- Missing from the code: class grants, two-person approval, requester exclusion, and target-later binding ("Approvals").
- Contradicting the spec: the approver check is a role list, not an "approve permission".
- Preflight and `target()` run before the authority check.
- No MCP client or pin-review workflow exists.

**5. Security, trust & isolation: 8.**

*Specified and realised:*
- "Only a Principal Instructs" sets two trust tiers.
- "Bound What a Convinced Model Can Do" defines a sticky untrusted mark and the rule of two, enforced: ALLOW becomes APPROVE (`attribution/rules.py:267-272`, `tools/impl/manager.py:286-287`).
- "Secrets Never Enter a Step":
  - brokers come first, and injected secrets go into a stripped environment;
  - redaction covers several encodings, with a holdback (`infra/transports/redaction.py`).
- "The Runtime" refuses isolation rather than weakening it. The account mode has careful process cleanup (`infra/workspaces/account.py`).
- "A Key per Session" seals content per session by envelope, as a storage decorator (`privacy/impl/sealed_steps.py`). Revoking the key erases content in place.

*What held it back:*
- No VM provider exists.
- Allowlist egress works only in the twin.
- No scan of workspace snapshots for secrets.
- `revoke_key` needs only WRITE.
- `KeyServiceInterface` has no destroy, so database backups keep unwrappable keys.
- Opening content needs no permission of its own, contrary to the spec.
- No threat model, by declaration ("What This Spec Does Not Cover").

**6. Bounds & cost control: 8.**

*Specified ("One Gate, Before the Call", "Breaches and Failing Closed", "Usage and Cost"):*
- a hold of the worst case before every model call and spending job;
- the hold is released only on proven non-billing;
- a refusal lists every breach;
- spend fails closed when the spender is unknown;
- reference cost is kept apart from native usage;
- the step guard can never be disabled.

*Realised:*
- The only two `client.stream` sites are both gated (`loop.py:531`, `windows/impl/manager.py:386`).
- `budgets/rules.py` holds the exposure arithmetic and the breach listing.
- `budgets/impl/gate.py` raises `SpenderUnknown` when no spender can be found.

*What held it back:*
- `call_shape` never sets provider-tool fees, thinking billed outside the output bound, or long cache writes (`windows/rules.py:194-209`), so those worst-case branches are dead.
- No tree-scope budget is ever created.
- Tree concurrency is stored but never enforced.
- "Usage retrieved later" has no code path.
- The `credential` parameter at the gate is unused.

**7. Model/provider independence: 7.**

*Specified ("Roles and Fills", "Fill Sets and Switches", "The Provider Boundary"):*
- a call site names a model role and never a model;
- a fill set is versioned;
- a switch is explicit and recorded as a `switched` step;
- thinking replays only to its own provider family.

*Realised:* Anthropic and OpenAI adapters plus a scripted twin (`integrations/model_providers/`).

*What held it back:*
- The loop always resolves with an empty `Eligibility()` (`loop.py:294`), so zero-retention and region constraints are never applied.
- The spec's own proof, "a second adapter that runs the whole loop unchanged", is not demonstrated: the loop tests use scripted twins named after the providers.
- There is no provider-adapter contract.

**8. Composition & extensibility: 7.**

*Strengths:*
- Agent kinds are versioned profiles over one loop ("Agent Kinds").
- A sub-agent is just a session with a parent, with a clean context, no escalation, one shared budget and one deadline ("Sub-Agents").
- Every dependency is an interface with a null object ("Null Objects").

*What held it back:*
- Extension is by *fork*: you render the scaffold and upgrade by merge ("Being Adopted").
- Control flow beyond done rules is deliberately absent; that narrowness is not penalised, and the fork model is.
- Handoff confirmation is approximated: the new session's objective simply does not wake it.

**9. Streaming, steering & observability: 6.**

*Specified ("Streams", "Steering"):*
- typed parts that add up to a step;
- emission never blocks the loop;
- inputs are durable on arrival and delivered at the next model call;
- controls travel out of band;
- a person can take over the environment.

*Realised:*
- The sink is told `opened` and `completed` in `finally` blocks.
- Pending inputs are a projection.
- Take-over and give-back exist (`loop.py:1352-1389`).

*What held it back:*
- "Tracing" promises OpenTelemetry GenAI spans, but no `gen_ai` span exists anywhere; the loop emits no spans.
- Artifact stream parts, urgent interrupts and the input digest are missing.
- Controls share the inputs' log, so they are not truly out of band.
- The only sinks are Null and Memory.

**10. Testability & conformance: 7.**

*Realised:*
- A scripted provider.
- 17 storage-contract mixins with about 292 cases, plus transport and key-service contracts.
- Injected clock, sleep and jitter.
- About 900 `om` tests.
- One test re-renders recorded requests and matches their hashes.

*What held it back ("Testing and Conformance"):*
- No replay driver.
- `new_id()` is hard-wired to `uuid7()` (`om/base.py`), so there is no deterministic id source.
- The kit lives under `tests/`, not in an importable package.
- No provider-adapter contract.
- The `agentic-check` checker decides 6 of 92 lenses, all marked partial.

**11. Adoption & operational cost: 5.**

*What adoption takes:*
- You copy about 58k lines of Python source plus a TypeScript portal, clients, Terraform and CI. It includes the guideline's tenancy, API and workers, and "Nothing is cut" ("Adopting the Guideline").
- You run Postgres, workers, a key service and a queue.
- You upgrade through a two-level merge chain: guideline, then engine, then platform.

*In its favour:*
- Coherent for a platform team.
- Skills scaffold tools, kinds, adapters and roles.

*What held it back:* heavy for anyone who wants only the loop, and there is no incremental-adoption path.

## Where it differs

**Stronger**
- **Recovery by declared tool effect, plus a writer epoch fenced in both storage and transport.**
  - *What it buys:* a crashed run never repeats an unsafe side effect, and a zombie run cannot write.
  - *The adjacents:* LangGraph re-runs nodes and leaves idempotency to you, with no OSS fencing. Pydantic AI core snapshots nothing mid-step. The Claude Agent SDK documents no side-effect recovery.
- **A worst-case budget hold before every call.**
  - *What it buys:* hard caps without overshoot, even under concurrency.
  - *The adjacents:* Pydantic AI checks cost after the response, best-effort. The Claude Agent SDK's `max_budget_usd` is a client estimate checked after. LangGraph has no money budget.
- **Policy keyed on class, effect and target, never on model text, plus the untrusted mark and the rule of two.**
  - *What it buys:* containment that holds even when injection succeeds.
  - *The adjacents:* Pydantic AI says approval "is not an authorization boundary". LangGraph OSS has no permission model.
- **Per-session envelope keys and erasure by revoking the key, with the shape kept.**
  - *What it buys:* audit and billing survive the deletion of content.
  - *The adjacent:* LangGraph's `EncryptedSerializer` uses one key for the whole store.
- **A pinned zone built only from principal messages.**
  - *What it buys:* standing instructions survive compaction without summaries becoming instructions.

**Weaker**
- **Observability.** Pydantic AI and LangGraph ship standard OTel/GenAI tracing, while agentic_core specifies it and ships none. *Cost:* the loop is a black box to standard tooling.
- **Extensibility.** LangGraph's channels and graph give arbitrary control flow and library-level extension. agentic_core extends by fork. *Cost:* every adopter owns a diverging copy.
- **Provider breadth and fallback ergonomics.** Pydantic AI's Model/Provider/Profile and `FallbackModel` with handlers are realised and broad. agentic_core's eligibility is unwired and its two-adapter proof is missing.
- **Testing doubles.** Pydantic AI's `TestModel`/`FunctionModel` and `override` are lighter to use than a rendered scaffold's twins.

**Broader or narrower**
- *Broader:* it owns attribution (actor, principal, spender), budgets across scopes, privacy at rest and workspace isolation. All three adjacents leave these to the application, or for sandboxing to the CLI or a harness.
- *Narrower:* one loop; no graph DSL; no hosted runtime.
- The Claude Agent SDK owns compaction and the tool suite but is tied to one vendor.
- Pydantic AI and LangGraph own durability only through an engine you add (Temporal or DBOS) or a checkpointer you pick.

**Simply different**
- **Specification and fork vs library and import.**
  - The fork wins when you build a platform you must audit and own end to end.
  - The library wins when you want upgrades and a small footprint.
- **A history that only grows vs mutable state.**
  - agentic_core's history wins for audit, replay and erasure without holes.
  - LangGraph's mutable state with `RemoveMessage` wins for arbitrary state machines whose state is not a conversation.
- **A closed loop vs an open graph.**
  - The Claude Agent SDK's closed loop trades control for a mature built-in harness.
  - agentic_core trades that harness for owned semantics.

## What I would change

1. **Make the spec and the code agree on the recovery row for a call awaiting approval.** Either amend "Durable by Default" to say an unsettled approval is answered `interrupted`, or make the code park again. Also remove the duplicate nudge limit. *Moves:* criterion 1 by +0.5 and criterion 2 by +0.5.
2. **Emit the promised OTel GenAI spans from the loop, or delete the promise from "Tracing".** The spans would cover agent invocation, model call and tool execution, with content capture off. *Moves:* criterion 9 by +1.5.
3. **Wire session eligibility into `resolve_fill_set`, and run the loop contract suite against both real adapters** through a provider-adapter contract. *Moves:* criterion 7 by +1.5 and criterion 10 by +0.5.
4. **Close the dead paths.**
   - Ship a native artifact-read tool, or stop telling the model one exists.
   - Set `provider_tools`, `thinking_outside` and the long cache write in `call_shape`.
   - Enforce tree concurrency, or remove the field.

   *Moves:* criterion 3 by +0.5 and criterion 6 by +1.
5. **Move `om/tests/contracts` into an importable conformance package, inject the id source, and add the replay driver** that "Testing and Conformance" names. *Moves:* criterion 10 by +1.5.
6. **Add `destroy` to `KeyServiceInterface`, gate `revoke_key` and content reads behind their own permissions, and fix the approver check to a permission.** *Moves:* criterion 5 by +0.5 and criterion 4 by +0.5.
7. **Cut the scaffold down to the engine.** Offer a render that drops tenancy, portal, Terraform and API, and leaves "an adopter drops what it does not need" to an opt-in. *Moves:* criterion 11 by +1.5.
8. **Trim approval features the code doesn't have.** Either implement class grants, two-person approval and requester exclusion, or retag them `optional` in "Approvals". *Moves:* criterion 4 by +0.5.

## Method

- **agentic_core repository:** cloned at commit `3922410e` ("Release 0.9.0"), 2026-10-07 10:19 PDT. It has 1,474 files.
- **The spec, `agentic_core_spec.md`:** read in full, all 1,717 lines. I also read `README.md`, `AGENTS.md`, `CHANGELOG.md`, `lenses/README.md` and `lenses/bounds.md` in full, and listed the other lens and skill files.
- **Scaffold:**
  - Listed every source file with line counts.
  - Two parallel audits read the engine's code against every spec section with file:line evidence: the loop, steps, windows, models and streams; then tools, policy, runtime, budgets, attribution, privacy, null objects and the checker.
  - I re-verified the load-bearing findings myself by grep: no `gen_ai` anywhere, `Eligibility()` at `loop.py:294`, the hard-wired `new_id`, no caller of `get_artifact`, tree concurrency stored and never enforced, and the approval-recovery branch.
  - No tests were run; dependencies are not installed.
- **Repository's own benchmark runs:** `benchmark/runs/*` holds earlier judge reports for this same prompt. I deliberately did not open their results, to avoid anchoring.
- **LangGraph:**
  - *Version:* `langgraph` 1.2.14 (2026-10-06); the repo was read at HEAD `40a2e6d`.
  - *Docs:* docs.langchain.com OSS LangGraph and LangChain pages (persistence, checkpointers, fault-tolerance, interrupts, streaming, subgraphs, testing, middleware, human-in-the-loop), read 2026-10-07.
  - *Thin evidence:* fencing (absent from the docs; inferred from source) and money budgets (not documented).
- **Pydantic AI:**
  - *Version:* tag v2.54.0 (2026-10-02/03); the docs were read from the repo's `docs/` at that tag and spot-checked against pydantic.dev/docs/ai.
  - *Not read:* the release notes (GitHub API blocked).
  - *Thin evidence:* the durability score rests partly on the separate Harness package.
- **Claude Agent SDK:**
  - *Version:* Python 0.2.164 (2026-10-06) and TypeScript 0.3.293 (2026-10-07).
  - *Docs:* code.claude.com/docs/en/agent-sdk pages (agent-loop, sessions, session-storage, permissions, hooks, subagents, cost-tracking, observability, secure-deployment, hosting) and the Claude Code sandboxing page.
  - *Thin evidence:* the TypeScript source is not public, so the loop and compaction internals are scored on documentation only. "Budget checked after the call" is inferred from the cost-tracking wording. Durability (4) and testability (3) are **low confidence**, because the docs are silent rather than negative.
- **agentic_core scores resting on thinner evidence:** criterion 11 is a judgment of effort, not a measurement. Criterion 8 depends on how heavily one weighs fork-based extension.
