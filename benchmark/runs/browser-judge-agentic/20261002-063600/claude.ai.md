# claude.ai

- URL: [redacted]
- Model: —
- Effort: —
- Sent: — (the page shows "2 days ago")
- Finished: —
- Status: ok
- Score: 80/100
- Read: the attached agentic_core_spec.md, 1,593 lines (the file chip on the page)
- Note: The answer is the copy button's text. It opens with the sentence written before the web search, joined to the score line without a break, and ends with the product's list of sources. The page shows no model or effort. The score line reads "Score: 80 / 100".

## Answer

I'll check the current state of the three competitors so the side-by-side is accurate, then give the evaluation.**Score: 80 / 100** as a design specification.

It is the most rigorous description of an agent *engine* I've seen. It treats durability, authority, money, and privacy as engine semantics rather than features to bolt on later. It loses points for three reasons: some of its hardest seams are left unspecified, its concept count and adoption cost are heavy, and nothing in it has been proven in code. If "exists, runs, is adopted" carried real weight (say 30 points instead of 6), it would land around 60 and rank last of the four today. Both numbers are honest. They measure different things.

## Criteria and scores

I scored agentic_core on what the spec guarantees. I scored each competitor on what it guarantees out of the box with its first-party integrations, not on what a strong team could build on top.

| Criterion (weight) | What I looked for | agentic_core | LangGraph + LangChain v1 | Pydantic AI | Claude Agent SDK |
|---|---|---|---|---|---|
| Conceptual integrity (10) | Crisp nouns, one home per concern, clean layering | 9 | 6 | 8 | 6 |
| Durability & recovery (14) | Persist before acting, replay that knows about side effects, refusing a stale writer | 12 | 9 | 10 | 6 |
| Security & trust (14) | Authority model, policy that ignores the model's claims, containing prompt injection, secrets, sandbox | 12 | 5 | 5 | 8 |
| Cost & bounds (9) | Enforced before the call, in money not just counts, limits shared across a tree of agents | 8 | 4 | 5 | 5 |
| Context engineering (9) | Compaction, prompt-cache stability, what survives a summary | 8 | 6 | 6 | 7 |
| Model/provider abstraction (7) | Neutral content model, explicit model switches, fallbacks | 6 | 5 | 6 | 2 |
| Human-in-the-loop, steering, suspend (8) | Approvals, input while running, waiting without holding compute | 7 | 6 | 4 | 6 |
| Multi-agent composition (6) | Sub-agents, handoffs, workflows, shared limits | 4 | 6 | 4 | 4 |
| Privacy & governance (6) | At-rest protection, erasure, audit without content | 5 | 2 | 2 | 2 |
| Testability (5) | Model stand-in for tests, replay, conformance suite | 4 | 3 | 5 | 2 |
| Precision & completeness (6) | Ambiguities, internal consistency, gaps | 4 | 4 | 5 | 4 |
| Adoptability (6) | Exists, small first step, ecosystem, interop | 1 | 6 | 5 | 6 |
| **Total** | | **80** | **62** | **65** | **58** |

## What earns the 80

- **The right primitive.** A request and its response are separate, immutable steps, and the response points back at the request. Outages, approvals that take hours, truncated streams, and crashes all fall out as ordinary cases. Competitors snapshot state (LangGraph) or append transcripts (Claude Agent SDK), and handle those cases ad hoc.
- **Recovery by declared effect.** Each tool declares whether it is read-only, idempotent, or unsafe. The idempotency key is the id of the request step. An unsafe call with an unknown outcome is recorded as `interrupted` and is never replayed, so the model has to check before retrying. This is the best paragraph in the document, and no framework does it.
- **Writer epochs.** Each run takes a fencing number on the session's cursor row, and every write is conditional on it. This is fencing-token discipline applied to agent loops, where two runs of one model call diverge rather than converge.
- **Prompt-cache economics as a design constraint.** The request is rendered in fixed layers from most to least stable. Each request records a hash of the rendered prompt, so a cache regression becomes a query. Model choices are pinned per session because switching models discards the cache. A model switch waits until no tool-use cycle is open, because of signed thinking blocks. This reflects real operating experience, not textbook design.
- **The trust model.** The spec separates who produced a step, on whose authority it runs, and who pays (actor, principal, spender). Only a principal instructs. Summaries render as data, which closes the path where injected text gets "laundered" into instructions through compaction. MCP annotations count as hints and server definitions are pinned by hash. A sticky, inherited "untrusted" mark puts into practice Meta's Agents Rule of Two: a session should not combine untrusted input, access to sensitive data, and the ability to change state or communicate externally without supervision such as human approval. None of the three competitors puts this into practice.
- **Money gated before the call.** Every call reserves its worst-case cost first and settles afterwards, like a payment authorization. A refusal lists every budget breached, not just the first. Budgets for a tree of sub-agents never copy money down. This is correct, and it is unique among these four.
- **Clear taxonomies.** "Not now" (park) is distinct from "failed", and `failed` (a conclusion of the work) is distinct from `inconclusive` (no conclusion). Each park names its reason, the action that clears it, and when it retries.
- **Crypto-shredding per session.** Content is encrypted under a per-session key, while shape (ids, types, sizes, costs) stays readable. Revoking the key erases the content and keeps billing and audit intact. Hashes are keyed per session, so a destroyed session's hashes reveal nothing. This is the right answer to erasure in an append-only log.
- **Null objects.** Every dependency has an implementation even when nobody provided one. Quiet ones may do nothing but must mark the loss (a result becomes *unverified*). Loud ones, for anything that acts on the world, refuse with an error the model reads. A quiet budget gate is refused outside local development.

## What costs it the other 20

1. **Parallel tool calls meet parking, and the spec doesn't say what happens.** An approval parks the loop while sibling calls proceed. But the major provider APIs require a result for every tool-use block before the next request, so the loop is blocked on the slowest approval anyway. The choices all have costs: render a synthetic "pending" result, hold the whole batch, or split it. Pydantic AI's own design notes on realtime approval found that a model may falsely claim an action completed while the call is still pending. This is the most common real approval scenario, and it's unspecified. Relatedly, a message sent to a session parked on approval waits for the resume. So "actually, don't do that" can only be expressed as deny or cancel.
2. **The fencing stops at the execution transport.** Integration and network tools hit third-party systems that know nothing about writer epochs. Recovery by asking for an outcome only works for calls that went through the transport. A run that lost its lease can still post the comment or open the pull request before it notices. The spec should do three things:
   - name that residual window;
   - require an epoch re-check immediately before any unsafe external call;
   - require integration adapters to forward the idempotency key or keep an outcome record.
3. **Worst-case holds will over-park trees.** Each hold covers the output bound at the highest rate, plus thinking and the long-context tier. That is safe, but often several times actual spend. Three concurrent children under one tree budget can park on budget while real spend is far below the line. A job's hold of rate × deadline can reserve a day for a ten-minute job. What's missing is shrink-to-fit: lower the output bound to what the remaining budget covers before refusing.
4. **Two "digests" quietly break two invariants.**
   - Rendering is claimed to be a pure function of the steps. But piled-up inputs, and standing instructions past a size bound, render as digests. If a model writes those digests, they must be recorded steps, or replay's prompt-hash check fails.
   - A model-written digest of a principal's instructions sits in the instruction tier, yet a model's paraphrase is no longer principal-authored. Make the digest extractive or principal-confirmed.
5. **The rule-of-two exemption is keyed on the wrong attribute.** The spec counts a pull request on the tenant's bound repository as not outward. If that repo is public, or has public CI logs, a PR body is an exfiltration channel. Policy already keys on target attributes, so the exemption should key on the target's visibility, not its type.
6. **Sessions that live for months need more versioning than this.** The running example revives a session on a model that didn't exist on day one. By then the step schema, the provider adapter, and the renderer have changed too. Replay's hash check only works if each `model_request` records the renderer and adapter versions that produced it. Upgrading old step content to a new schema isn't addressed.
7. **It rebuilds a durable-execution engine and hands the hardest part to the adopter.** History, replay, timers (park retry times), signals (the inbox), and fencing are a subset of Temporal or DBOS. Liveness and leases are left to "the adopter." The LLM-specific semantics on top are the real contribution: effect-based recovery, cache-aware rendering, budget holds. Those would port. The spec should say whether its interfaces can be implemented over Temporal or DBOS. That is the shortest route to a trustworthy implementation, and the route Pydantic AI took.
8. **Gaps the market already expects.**
   - Validated structured output with retry on validation failure; the result gate is not that.
   - A model for memory across sessions; "recalled knowledge" is only classified as data.
   - Interop protocols such as A2A and AG-UI.
   - Realtime and voice.

   Some of these rightly belong to products. But the "does not cover" list doesn't name them, so a reader can't tell a deliberate exclusion from an oversight.
9. **Surface area and coupling.**
   - It coins about 25 terms, and nearly all untagged text is normative.
   - It is pinned to an external guideline through dozens of links and rule IDs (STO-32, ASY-13), so it can't be read standalone.
   - Adopting it means merging two scaffolds. That's coherent for an in-house platform family, but it's the opposite of "install and write ten lines."
   - Minor: "the engine is pure" means dependency-injected, not pure. The illustrative loop is serial while the prose describes concurrency with partial parking.

## Side by side

**Pydantic AI: closest in philosophy.** It is typed, provider-neutral, and built around dependency injection. Its test models are what agentic_core's scripted provider aims to be. It delegates durability: once a durability capability is attached, each model and tool call becomes a durable activity, across eight supported engines. It also explicitly separates keeping one run alive from storing chat threads. That is the opposite of agentic_core, where one history serves both. Approvals use deferred tools: calls needing approval come back as the run's output, and results are fed into the next run by tool-call id. Its docs concede that approval doesn't replace authorization inside the tool itself. That is precisely the gap agentic_core's policy by class and target fills. It lacks effect-aware recovery (a durable engine retries by policy, and duplicate side effects are your problem), attribution, a taint mark, money holds, and crypto-erasure. agentic_core lacks everything that ships.

**Claude Agent SDK: closest in what it is.** Like agentic_core, it is a harness that owns the loop: the same execution loop that powers Claude Code. It offers permission modes, an approval callback, lifecycle hooks, session resume and fork, and pluggable session storage; turn and dollar caps end a run with a result naming the cap. Once spend hits the cap, new subagents fail to spawn and background ones are stopped. It also ships a real sandbox, with network access limited to allowed domains.

So it has a working budget and a working sandbox, which agentic_core only specifies as interfaces. But the budget is a cap checked as spend accrues, not a hold before the call. Sessions are transcripts written as local JSONL, optionally mirrored to your backend through an adapter: resumable, but with no recovery rules for an interrupted tool call and no fencing. It is Claude-only by design. Its edge is that it's battle-tested on one of the most-used coding agents.

**LangGraph + LangChain v1: most adopted, different abstraction.** It's a graph orchestrator, and it wins on composition: arbitrary graphs, subgraphs, supervisors. It checkpoints state at each super-step, with exit, async, and sync durability modes. Temporal's critique is that these checkpoints preserve data rather than execution: when the process dies, detecting it and re-entering the graph is left to you. LangChain v1 middleware covers human approval of tool calls, summarization, and model-call and tool-call limits, counted per thread or per run. Those are counts, not money. Steering mid-run ("double texting") is a LangSmith Deployment feature, not part of open-source LangGraph, and its interrupt strategy can leave a started-but-unfinished tool call that your graph must clean up. That is exactly the case agentic_core's recovery table settles.

## What would move it to 90

- Specify how parallel calls behave when one parks, and how steering works while parked.
- Extend fencing and idempotency to integration adapters, and name the residual window.
- Add shrink-to-fit holds and a sizing rule for job holds.
- Make digests extractive or recorded steps, and record renderer and adapter versions on every request.
- State whether an implementation over Temporal or DBOS can pass the conformance kit.
- Write a one-page minimum profile: which sections a laptop adopter can replace with null objects.
- Ship the scripted provider, the conformance kit, and one implementation that runs the running example end to end on two providers. The spec names "a second adapter runs the loop unchanged" as its own proof; it should meet it.

I can put this evaluation into a doc if you want to share it with reviewers.

Sources:
- [Pydantic AI on GitHub](https://github.com/pydantic/pydantic-ai)
- [Pydantic AI: Durable Execution](https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/)
- [Pydantic AI: Deferred Tools](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/)
- [Pydantic AI: tools API](https://pydantic.dev/docs/ai/api/pydantic-ai/tools/)
- [Pydantic AI issue #7301: interactive approval in realtime sessions](https://github.com/pydantic/pydantic-ai/issues/7301)
- [Claude Agent SDK: How the agent loop works](https://code.claude.com/docs/en/agent-sdk/agent-loop.md)
- [Claude Agent SDK: Configure your agent](https://code.claude.com/docs/en/agent-sdk/configuration)
- [Claude Agent SDK: Persist sessions to external storage](https://code.claude.com/docs/en/agent-sdk/session-storage)
- [Claude Agent SDK provider options (promptfoo)](https://www.promptfoo.dev/docs/providers/claude-agent-sdk/)
- [LangGraph: Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)
- [Temporal: LangGraph plugin adds durable execution](https://temporal.io/blog/temporal-langgraph-plugin-durable-execution)
- [LangChain: Prebuilt middleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in.md)
- [LangChain middleware reference](https://reference.langchain.com/python/langchain/agents/middleware)
- [LangSmith: Double texting](https://docs.langchain.com/langsmith/double-texting)
- [Meta introduces Agents Rule of Two](https://blockchain.news/news/meta-introduces-agents-rule-of-two-for-enhanced-ai-security)
