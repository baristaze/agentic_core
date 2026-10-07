# claude.ai

- URL: [redacted]
- Model: Opus 5.5
- Effort: High
- Sent: 2026-10-07T14:52:40Z
- Finished: 2026-10-07T15:30:29Z (the read-back time, by date -u; the page shows no duration for the response)
- Status: ok
- Score: 77/100
- Read: commit a5ad9cc (release 0.8.0, 2026-10-07)
- Polls: 29
- Note: the composer held a draft of another prompt on arrival, cleared before the paste; the "Manual" control beside the chip reads "Claude asks before using new tools", a tool-approval mode, not a line that the product can act on the machine; the prompt went as one pasted card ("Pasted text.txt"); a Share dialog showed open in the tab at poll 4, not opened by this run; nothing in it was clicked, and it was gone at the next look; timed out: thirty minutes from the send, the page still read "Claude is responding" at 29m 57s, "Step 1 of 4", its last step "Scoring sandbox safety, cost limits, and observability for this agent", with no score line yet; recorded timed-out at the skill's thirty-minute bound with the answer still running; the owner raised the bound to three hours, and the finished answer was read back from the conversation at 2026-10-07T15:30:29Z; at the read-back the page text no longer held "Claude finished the response", and the page showed the answer done: no stop control, the answer's copy and retry row under it, and every task of its progress panel Done; the finish time is the read-back time, as the page shows no duration for the response, only "38 minutes ago", the send

## Answer

I'll read the agentic_core repository in full, then research the closest adjacents' current docs and score everything under one frozen rubric.

Score: 77/100

## Classification

- **What it is:** a normative specification (`agentic_core_spec.md`) of one tool-call loop, made durable, bounded, steerable, private and auditable. It comes with a reference kernel in `scaffold/acme_root/` that runs and is tested: the engine namespaces under `om/`, two provider adapters under `integrations/`, and transports and workspaces under `infra/`.
- **Abstraction level:** it sits below an agent product and its prompts, and above provider SDKs, sandboxes and the database. Its unit is the loop and the record the loop writes.
- **Architectural role:** it is the execution core a platform embeds. In the spec's Scope diagram, `[redacted]` embeds it and a product is an instance of that platform.
- **What it owns:**
  - the step record and its writer-epoch fence
  - recovery by tool effect
  - deterministic rendering and compaction
  - model roles, fills and switches
  - the tool contract, policy and approvals
  - one budget gate held before every call
  - parks and their unlocks
  - actor, principal and spender
  - sealing content with a key per session
- **What it delegates:**
  - general software design, to *swe_guidelines*, cited about 30 times
  - fleet placement, money units and judging behaviour, to `[redacted]`
  - prompts, tools and agent kinds, to the product
  - liveness and leases, to the adopter's work queue
  - default values, wire formats and the threat model ("What This Spec Does Not Cover")
- **What it is not:**
  - a prompt or chain library
  - a graph or workflow DSL
  - a general durable-execution engine
  - a hosted service
  - an installable package: you adopt it by rendering a copy of a monorepo (`scaffold/new.py`, `base.py`)
- **Category:** a *durable agent-loop runtime specification, with a reference kernel*.
  - "Agent framework" overstates what it offers application authors (no tool library, no orchestration DSL) and understates what it guarantees.
  - "Durable-execution engine" is too general: it runs exactly one loop, with semantics specific to agents (effects, payers, trust tiers).

## Adjacents

- **LangGraph 1.2.14:** a checkpointed runtime with `interrupt()`, streaming and subgraphs. It overlaps Durable by Default, Parking, Steering, Streams and Sub-Agents.
- **Pydantic AI 2.54.0:** a typed loop that is neutral about providers, with deferred-tool approvals, `UsageLimits` and durability backends (Temporal, DBOS, Prefect). It overlaps Models, The Tool Contract, Approvals, Bounds and Budgets, and Testing.
- **Claude Agent SDK (Python 0.2.164, TS 0.3.292, bundled CLI 2.1.292):** it owns a production agent loop with layered allow/ask/deny permissions, a sandbox, auto-compaction, and sub-agents that start from a clean context. It overlaps Policy, Approvals, The Runtime, Compaction and Sub-Agents.
- **Not chosen:**
  - Temporal: it overlaps durability only.
  - OpenAI Agents SDK: its overlap is a subset of the Claude SDK's and Pydantic AI's.

## Rubric

This rubric is fitted to the category, so it weights durability, governance and accounting heavily. That favours what `agentic_core` set out to do. C4, C9 and C11 carry the weight where the adjacents are strong.

| # | Criterion | Measures | 10/10 looks like | Weight |
|---|---|---|---|---|
| C1 | Conceptual model & separation of concerns | How crisp and minimal the core nouns are, and whether model, engine, record and policy are cleanly divided | Few orthogonal concepts, each with one owner; nothing ambiguous between outcome, state and record | 11 |
| C2 | Durability, recovery & failure semantics | What is persisted, and when; crash recovery; fencing; the error taxonomy | Persist-before-act; recovery decided per side-effect class; fencing of stale writers; every error kind given a defined response | 13 |
| C3 | Context & state management | History, windows, compaction, cache stability, large results | An immutable source of truth; windows are views over it; deterministic, cache-stable rendering; compaction that loses no record | 9 |
| C4 | Model/provider independence | The provider boundary, switching, fallback, provider quirks | A neutral content model; adapters that name what does not survive translation; explicit, recorded switches; broad coverage the design makes cheap | 7 |
| C5 | Tool contract, policy & approvals | Tool metadata, the policy engine, binding of approvals | Typed tools that declare their power and side effects; layered policy independent of model text; approvals bound to the exact call | 10 |
| C6 | Trust, security & isolation | Injection stance, secrets, sandboxing, data at rest | Trust tiers in rendering; bounds on what a convinced model can do; secrets never in context; isolation that refuses rather than degrades; per-unit encryption | 10 |
| C7 | Bounds & cost accounting | Budgets, limits, usage records | A gate before the call that holds the worst case; scoped budgets; billing records apart from content; a backstop limit that cannot be disabled | 8 |
| C8 | Control flow, steering, streaming & composition | Inbox and controls, parking, streams, sub-agents, concurrency | Durable mid-run input; out-of-band controls; non-blocking typed streams; bounded composition with shared deadlines; parallelism where safe | 9 |
| C9 | Testability, determinism & observability | Twins, replay, conformance, telemetry | A scripted model twin; deterministic replay; an importable conformance kit; injected clock and ids; standard telemetry that carries shape only | 8 |
| C10 | Contract clarity & spec–implementation fidelity | Precision, self-containment, whether docs and code agree | A precise, self-contained contract; code and docs never diverge; rules enforced mechanically | 7 |
| C11 | Adoptability | Ergonomics, interop, escape hatches, operating cost | Adopt it in an afternoon inside an existing app; every part swappable; standard protocols; small footprint | 8 |
| | | | | **100** |

## Scores

`*` marks a low-confidence score: the architecture is not documented well enough to score that criterion fully.

| Criterion | Weight | agentic_core | LangGraph | Pydantic AI | Claude Agent SDK |
|---|---|---|---|---|---|
| C1 Conceptual model | 11 | 9 | 7 | 8 | 6 |
| C2 Durability & recovery | 13 | 9 | 7* | 6 | 4* |
| C3 Context & state | 9 | 8 | 6 | 6 | 7 |
| C4 Provider independence | 7 | 7 | 6 | 9 | 3 |
| C5 Tools, policy, approvals | 10 | 9 | 5 | 6 | 7 |
| C6 Trust & isolation | 10 | 8 | 4 | 5 | 6 |
| C7 Bounds & cost | 8 | 9 | 4 | 6 | 5* |
| C8 Control & composition | 9 | 7 | 8 | 6 | 7 |
| C9 Testability & observability | 8 | 7 | 7 | 9 | 4* |
| C10 Contract fidelity | 7 | 6 | 7 | 8 | 6* |
| C11 Adoptability | 8 | 4 | 8 | 8 | 7 |
| **Weighted /100** | | **77** | **63** | **69** | **57** |

The Claude SDK's raw total is 56.5, rounded half up to 57.

The scopes differ:
- LangGraph is a general graph runtime.
- The Claude SDK is a client of a closed CLI process.
- `agentic_core` also owns tenancy and privacy, which the others leave to the application.

## agentic_core, criterion by criterion

**C1, 9.** "The model chooses; the engine does" (The Engine and the Brain) splits judgment from durability, bounds and the record.
- A step is the only record, and a loop has no table (Loops, Runs, and Sessions).
- Five outcomes are kept apart from a park, which writes no outcome (Parking).
- Actor, principal and spender are three separate answers (Who Is Who).
- Every dependency has a null object, quiet or loud (Null Objects).
- The code keeps this shape. Decisions live in pure `rules.py` modules, and side effects live in `impl/`; `agents/impl/loop.py` is a thin driver over `loop_rules.py`.

What holds it back: the engine is not separable from the guideline's ideas. Its operations take `TenantContext`, and storage is split by database role. The spec has 26 terms in Concepts at a Glance; each earns its place, but together they are dense.

**C2, 9.** The table in Durable by Default decides recovery by effect.
- A lost model request is closed `abandoned`, and its hold settles in full.
- An `unsafe` call with no answer is recorded `interrupted`, with its outcome unknown.
- The writer epoch is a compare-and-set on the cursor row, and every fenced append depends on it (`steps/storage/impl/postgres.py`, `_append_statement` and `_begin_statement`; ADR 1002). Transports refuse commands from a stale epoch.
- `tools/tool.py` allows an `unsafe` tool one command per call, so the transport's record of that command *is* its outcome after a crash. This is a subtle and well-chosen constraint.
- Provider error kinds each map to an action (Provider Errors).
- Tests cover a real kill signal in the middle of a call (`workers/session_runner/tests/test_end_to_end.py`).

What holds it back:
- The spec says a lost run's call that was awaiting approval "parks again". The code answers it as `interrupted` instead (`loop.py` lines 769–773, ADR 1009).
- How provider-side tools recover is stated only in an agents-only comment.
- Liveness is entirely the adopter's job.

**C3, 8.** History is append-only, and `UPDATE`/`DELETE` are revoked in the steps migration.
- A window is a view sized per fill, and its cuts are consistent (`windows/rules.py`, `consistent_cuts`).
- Rendering is a pure function with a fixed order from the most stable layer to the least, plus a hash of the rendered prompt (Rendering).
- The pinned zone is built only from what principals wrote (The Pinned Zone).
- Compaction retries once on overflow (Compaction).

What holds it back:
- Large Results and Retrieval says a read tool pages through an artifact, but `get_artifact` is reached only from tests; no engine tool exposes it.
- The digest for non-waking inputs (The Inbox) is absent.

**C4, 7.** The design is strong:
- roles, fills and a resolver
- versioned fill sets, and a `switched` step for every switch
- rules for when thinking may be replayed
- stop reasons on every response
- error kinds read from the message as well as the status code (Models)

There are real Anthropic and OpenAI adapters that speak raw HTTP, and a scripted twin (`integrations/.../model_providers/`).

What holds it back:
- The spec's own proof, "a second adapter that runs the whole loop unchanged", is not demonstrated. Loop tests register the scripted twin under both provider names.
- `LoopOptions.credential` is fixed to `"platform"`, and the loop resolves with an empty `Eligibility()` (`loop.py` lines 112 and 279). So a tenant's own key and zero data retention are not wired.
- `model_unavailable` falls back but never re-resolves the role.

**C5, 9.** The tool contract declares class, effect, mode, whether the tool is interruptible, and an optional preflight (The Tool Contract). `decide()` in `tools/rules.py` is the policy engine:
- It reads tool, class, effect and target only. `PolicyCall` has no field for the model's text.
- The most specific rule wins across the kind's defaults and the tenant's layer, and platform ceilings cap the result.
- A call that no rule matches waits for a person, so the default is fail-safe.
- An approval is bound to the tool plus a hash of its input, and it expires.
- MCP tool definitions are pinned by hash, and the server's annotations are only hints (`tools/mcp.py`).
- A sub-agent's call is decided under every kind above it, and the strictest decision holds.

What holds it back: the approval variants the spec allows (grants per class, two approvers, binding to a target chosen later) are not built.

**C6, 8.**
- Two trust tiers: data is quoted inside a labelled `<data origin=…>` element with its delimiters escaped (`windows/rules.py`, `data_block`).
- A sticky untrusted mark and the rule of two (Bound What a Convinced Model Can Do).
- Redaction holds back output as long as the longest secret (`transports/redaction.py`).
- Content is sealed by envelope encryption with a key per session, and revoking the key erases it while the shape stays (Privacy; `privacy/impl/sealed_steps.py`).
- A workspace weaker than the isolation spec is refused, never substituted (The Runtime).

What holds it back:
- The broker the spec calls its first answer exists only as a null and a twin.
- The container provider refuses an egress allowlist, which is one leg of the rule of two.
- There is no VM provider.
- The threat model is placed out of scope.

**C7, 9.** One gate stands before every model call and every job that spends.
- It holds the worst case, including cache-write and long-context rates.
- A refusal lists every breach, and an unknown payer fails closed.
- A hold settles in full unless the provider provably did not bill (One Gate, Before the Call; `budgets/impl/gate.py`).
- Usage records hold no content and survive a purge (Usage and Cost).
- The step guard can never be disabled.
- Guards park, bounds fail, and yields hand the loop to a new run (Time, Steps, and Streaks).
- A child draws on what its tree has left.

What holds it back: the tree's concurrency limit is stored but not enforced (`agents/rules.py`).

**C8, 7.** The steering design is good:
- an inbox that is durable on arrival, delivered at the next model call
- controls out of band, including take-over
- typed stream parts that never block the loop
- sub-agent trees with a clean context, a shared deadline and a cascading cancel

What holds it back:
- The spec says calls "run concurrently when their tools allow it", but `_tool_turn` settles them one after another (`loop.py` line 645).
- Controls are read by polling every 500 ms.
- Artifact stream parts are absent, and no production stream carrier is wired (`root.py` defaults to the null sink).
- Hand-off and take-over have no API routes.

Narrowness to one loop is intentional and is not penalised.

**C9, 7.**
- A scripted provider that can raise every error kind.
- An injected clock, sleep and jitter.
- A replay test of the prompt hash (`test_windows.py`).
- Contract classes for storage, transports, key service and outage signal.
- I ran the unit suite on a copy of the scaffold: 4,674 passed. The one failure is an artifact of my copy not being a git checkout.

What holds it back:
- The conformance kit is pytest modules, not a package an adopter imports, and it has no contract for the workspace provider or the provider adapters.
- There is no deterministic id source: `new_id()` is `uuid7()`.
- The OpenTelemetry GenAI spans promised in Tracing do not exist.

**C10, 6.**
- The spec tags each section `core`, `default` or `optional`, and its Principle boxes form a checklist.
- 92 lenses are held to the spec by `scripts/check_lenses.py`, and `agentic-check` decides six of them mechanically.

What holds it back:
- Four places where spec and code diverge, listed under C2, C4, C5 and C8, plus approver roles versus "the approve permission".
- The spec still calls the lenses and the kit "planned".
- `scaffold/README.md` is still the guideline's text.
- Defaults are declared out of scope, yet a default tree is specified.
- Rule IDs such as ASY-13 and STO-28, and the rules behind contexts, roles and the queue, live only in the external guideline.

**C11, 4.** Adopting it means rendering a copy of a monorepo of about 198k lines, roughly 73% of it inherited from the guideline: a portal, Terraform, WorkOS, tenancy. It also brings Postgres row-level security and multiple logins, workers and an outbox. You upgrade by merging a `scaffold` branch.
- The escape hatches are real: every capability is an injected interface with a null object.
- But there is no installable package and no MCP client, and it is Python only.
- You cannot drop the loop into an existing application.

## Where it differs

- **Stronger:**
  - *Recovery.* `agentic_core` decides recovery by the tool's declared effect and fences stale writers. On a crash, LangGraph re-runs the node, Pydantic AI says "automatic execution recovery is not implemented", and the Claude SDK does not document what happens. This buys no duplicated unsafe side effects after a crash.
  - *Budgets.* It holds the worst case before the call. Pydantic AI checks tokens and cost after the response, and the Claude SDK's `max_budget_usd` evidently does too. This buys no overshoot, and no two sessions slipping under one line.
  - *Approvals.* An approval is bound to a hash of the call's input. LangGraph's HITL middleware matches decisions by position, and Pydantic AI's approvals are unsigned by design.
  - *Encryption.* A key per session lets it erase content while keeping its shape. LangGraph encrypts whole checkpoints; the other two leave it to the application.
- **Weaker:**
  - *Provider layering.* Pydantic AI's Model/Provider/Profile layering makes new providers cheap and proves the boundary across about 37 of them. `agentic_core`'s claim of provider neutrality rests on two adapters, and only one is exercised end to end.
  - *Telemetry and conformance.* Pydantic AI emits GenAI semantic-convention spans, and LangGraph ships a checkpoint-conformance package. `agentic_core` has neither, so adopters cannot verify their own implementations.
  - *Adoption.* `pip install` versus rendering a monorepo. The cost is that existing applications are shut out.
- **Broader or narrower:**
  - *Broader.* It owns attribution (actor, principal, spender), tenancy, storage roles, sealing and a ledger, all of which the adjacents leave to the application.
  - *Narrower.* There is no graph or workflow composition beyond one loop and sub-agents (LangGraph has both). It ships no built-in tool set (the Claude SDK ships Read, Edit, Bash and others) and no MCP client.
- **Simply different:**
  - *Record.* An append-only history plus projections, versus LangGraph's mutable state with reducers and forking. Append-only wins for audit, replay and erasure by key; mutable state with forks wins for exploratory editing and time travel.
  - *Who owns durability.* `agentic_core` owns the loop's durability; Pydantic AI hands it to Temporal or DBOS. Owning wins when the recovery semantics are specific to agents. Delegating wins when the team already runs a workflow engine.

## What I would change

1. Reconcile the spec with the code in the same change, and remove "planned" from The Repository. The divergences are: approval recovery, concurrent execution, `model_unavailable` re-resolution, and approver permission. Either build each one or rewrite the spec to the code. Moves C10 by about +2.
2. Extract the engine into an installable package: the engine namespaces, the provider adapters and the infra capabilities. Give it narrow ports for the context and storage, and make the rest of the guideline scaffold an optional host. Moves C11 by about +3, C1 by +0.5.
3. Ship the conformance kit as an importable package. Add contracts for the workspace provider and the provider adapters, and run the whole loop against recorded fixtures of the second real adapter. Moves C9 by +1, C4 by +1.
4. Emit the OpenTelemetry GenAI spans the spec already promises, carrying shape only, and inject an id source beside the clock. Moves C9 by +1.
5. Either run tool calls concurrently where their effect allows, or state "sequential" as the default with its reason. Moves C8 by +1.
6. Wire the per-call credential and the session's eligibility, or tag The Tenant's Own Key and zero data retention as not yet built. Moves C4 by +1.
7. Ship one real egress-allowlist path, a broker or a container provider behind a proxy, because the rule of two leans on it. Moves C6 by +1.
8. Expose the artifact-paging tool and API routes for hand-off and take-over. Moves C3 and C8 by about +0.5 each.
9. Inline a glossary of the guideline rule IDs and terms the spec depends on. Moves C10 by +0.5.
10. Move `benchmark/` out of the repository. It is LLM products scoring this repository, and the results sit inside the very thing being judged. It is not architecture. Small effect on C10 and on coherence.

## Method

- **agentic_core, commit `a5ad9cc` (release 0.8.0, 2026-10-07), read on 2026-10-07:**
  - Read in full: the spec, the README, `AGENTS.md`, `CONTRIBUTING.md`, `SECURITY.md`, the Makefile, `agents/impl/loop.py`.
  - Read in part: step storage and its migration, the budget gate, `tools/tool.py`, the policy rules and types, `agents/types/kind.py`, `windows/rules.py`.
  - A delegated sweep with file citations covered the lenses, skills, agents, scripts, the scaffold's ADRs, integrations, infra, workers, migrations and tests. I spot-checked its main claims against the code.
- **Execution:** I ran the scaffold's unit tests over the memory implementations (4,674 passed). I did not run the integration or end-to-end suites, which need the compose stack.
- **Prior judgments:** I deliberately did not read the earlier judges' answers under `benchmark/runs/`, to avoid anchoring.
- **LangGraph:** langgraph 1.2.14 (PyPI, 2026-10-06), langchain 1.4.3, docs.langchain.com and the source, read on 2026-10-07. C2 is low confidence because cross-process fencing in the open-source checkpointers is not documented, and Agent Server is closed source.
- **Pydantic AI:** 2.54.0 (PyPI, 2026-10-03), the docs at that tag and the source. The Harness (0.x) features are counted only where the docs specify them.
- **Claude Agent SDK:** Python 0.2.164 and TS 0.3.292 (2026-10-06), the docs on code.claude.com and the Python source. The TS source is not public and the loop lives in the closed CLI, so C2, C7, C9 and C10 are low confidence.
- **Thinnest evidence on agentic_core:** C6 rests partly on stubs (the broker, the VM provider). C11 is my judgment of what adopting it costs, not a measurement.
