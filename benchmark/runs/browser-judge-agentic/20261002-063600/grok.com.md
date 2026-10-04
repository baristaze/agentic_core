# grok.com

- URL: [redacted]
- Model: —
- Effort: —
- Sent: — (the page shows no time)
- Finished: — (the page shows "Worked for 42s")
- Status: ok
- Score: 84/100
- Read: the attached agentic_core_spec.md
- Note: The answer is the copy button's Markdown. The page shows no model, effort, or time. It gives its score in prose: "The spec scores 84 out of 100".

## Answer

The spec scores 84 out of 100 as an engine contract. It is the right cut for a long-lived, multi-tenant agent runtime, and it is stricter on the failure modes that actually lose money and data than the three frameworks that already own adoption. It is not yet an implementable system: several invariants stop at a principle, and nothing here has been forced through a conformance suite.

The score is for the document as a design, not for a shipped library. `agentic_core` is an engine specification that a platform is meant to embed. LangGraph, the Claude Agent SDK, and Pydantic AI are developer frameworks. Time-to-first-agent is the wrong axis. The right one is whether the engine boundary, the record, and the recovery rules are specified tightly enough that two adopters would build the same machine.

## Criteria

1. Boundary. What the model decides versus what the engine owns.
2. Record and recovery. What is durable, what a crash may repeat, what a stale writer may do.
3. Failure taxonomy. Park, fail, inconclusive, cancel, and error as different outcomes.
4. Trust. Who may instruct, who may act, who pays, and what a convinced model can still do.
5. Privacy and deletion. What is sealed, what stays readable, what erasure means.
6. Bounds. Budget, deadline, and step limits checked before spend, not after.
7. Context. Window, compaction, and cache stability without rewriting history.
8. Model neutrality. A call names a role; a switch is explicit.
9. Testability. Replay, twins, and a conformance bar.
10. Completeness of the contract. Enough to implement without inventing the hard parts.

## Why 84

The invariants are the ones a production engine actually needs, and they are stated as rules rather than features.

The model chooses; the engine does. Durability, policy, attribution, and the record are engine semantics. That is the opposite of borrowing a framework loop and hoping its checkpoint happens to match your audit. A request and its response are two steps, because a response may arrive later, in parts, or never. Groupings reference steps and never copy them, which is the only stance that survives sealed content. Five outcomes end a loop, and a park is none of them. A session never ends. Isolation is refused, never weakened. A secret in an agent's process is assumed disclosed and never enters a step. Only a principal instructs. Every model call and every spending job passes one gate before it starts, holding the worst case. Content is sealed per session; shape is not.

Recovery is specified by effect, not by "resume and hope." A stored response is reused. A model request with no response is abandoned and retried. A `read_only` or `idempotent` tool runs again under the request step's id. An `unsafe` tool is never repeated unless the transport can answer the outcome; otherwise the step is `interrupted` and the model must verify. A writer epoch on the session cursor refuses a run that lost its claim. That is fencing, not a lease comment.

The trust model is equally concrete. Actor, principal, and spender are three answers. The agent acts and holds no authority. Data is quoted and labelled. An untrusted mark sticks after the first read and follows children. The rule of two forces a person onto outward calls when a marked session also holds private data and can leave its allowlist. Policy keys on class and target, never on what the model claims. Approvals bind to the exact call.

The writing matches the claims. Tags distinguish `core` from `default` and `optional`. Principle boxes are a review checklist. Scope excludes the fleet and the product. Deviations from the adopted guideline are named. Null objects are quiet for observers and loud for capabilities, so a missing workspace cannot report success.

The missing 16 points are holes in the contract, not taste.

Concurrency is underspecified. Tool calls in one response may run together, but `seq` is gapless from one cursor row, and every append is conditional on the writer epoch. The protocol for two tool completions, a control, and a stream sink hitting that row is not closed. A budget hold across a tree with children drawing on one ledger has the same gap: the gate is right, the race is not drawn.

The rule of two is a principle without a decision procedure. "Private data," "work product," and "egress beyond the allowlist" are sketched. Two implementers will draw the line in different places, which is exactly where prompt injection becomes a policy bug.

There is no threat model, by the spec's own omission. For a document whose core claims are isolation, secrets, and trust tiers, that is a real hole. Mechanisms without an adversary list are hard to audit.

Wire formats, schemas, and default thresholds are deferred. That is honest, and it also means this is not yet a conformance target. The planned lenses and kit are named, not present. "Persist before you proceed" still needs the append transaction, the outbox handoff, and the epoch compare-and-set written as one protocol.

Compaction is `default` and depends on a summarizer. Rendering summaries as data is the right rule. It does not say how a bad summary is detected, or what happens when the pinned-zone digest drops a standing instruction that still fits only as a citation. Cache-stable rendering is specified; summary fidelity is not.

The secret deviation is acknowledged and still sharp. Broker first, then a short-lived scoped inject into one stripped process, with stream holdback. Holdback length and encoded forms are the usual place this fails, and they sit in an agents-only note rather than a testable rule.

## Side by side

| Criterion | agentic_core | LangGraph | Claude Agent SDK | Pydantic AI |
|---|---|---|---|---|
| What it is | Engine spec: one loop, durable record, platform embeds it | Library: you draw the graph | Product harness as a library: Claude with a computer | Typed agent library; durability is an attached engine |
| Who owns the loop | The engine. The model only chooses | You do, as nodes and edges | Anthropic does | The framework does, inside `Agent.run` |
| Durability | Append-only steps, persist-before-act, recover by effect, writer epoch | Checkpoint per superstep; resume re-runs the node | Session JSONL; resume restores the conversation, not the filesystem | Temporal, DBOS, Prefect, and others as capabilities |
| Unsafe side effects | Never replayed unless the transport reports the outcome | Your problem: nodes must be idempotent | Not a stated effect class | Application code |
| Human wait | Park with reason, unlock, and retry time; holds no runtime | `interrupt()` plus a checkpointer | Permission mode, hooks, `AskUserQuestion` in-loop | Human-in-the-loop where the durable engine supports it |
| Budget | One gate, worst-case hold, fail closed | Not built in | `max_budget_usd`, `max_turns` | Gateway and cost monitoring; gate semantics are yours |
| Trust | Principal vs data, class policy, untrusted mark, rule of two | You build it | Permission order: hooks, allow-list, mode | Hooks, capabilities, guardrails; authorization still yours |
| Privacy | Per-session key; revoke key destroys content, keeps shape | State lands in the checkpointer, often plaintext | Local session files; content capture is your config | Traces via Logfire; sealing is not the model |
| Models | Role, then a versioned fill; switch is a step | Any model | Claude harness; other endpoints are unsupported in effect | Model-agnostic, typed output |
| Context | Window is a view; pinned zone is principal text only | You manage state and reducers | Built-in compaction and a PreCompact hook | Capabilities for memory and context |
| Multi-agent | Child session, clean context, no escalation, one tree budget and deadline | Nested graphs, supervisor or swarm | Subagents as tools | Capability-based delegation; shared budget is yours |
| Adoption | None. Spec only | The default production graph runtime | The default coding-agent loop | The default typed Python agent layer |

LangGraph is the closest on durability and the furthest on philosophy. A checkpointer plus `interrupt()` is how teams pause for days and resume after a crash. The resume contract is weaker than this spec. A node re-executes from the top, so anything before the interrupt must be idempotent, and a 2026 measurement of LangGraph 1.2.9 found exactly-once across interrupts and at-least-once across a real kill, on one API, with no machine-checkable contract. `agentic_core` writes that contract: effect class, idempotency key equal to the request step, fencing token, no silent replay of `unsafe`. LangGraph wins when the business process is a known graph. This spec correctly refuses to be that graph. It will be the wrong tool for a payment flow whose edges are rules, not model choices.

The Claude Agent SDK is the closest on the loop shape. Gather context, act, verify, repeat; sessions; compaction; subagents; permissions; budgets; streaming. It is a harness aligned to one model family, with a real computer attached. `allowed_tools` auto-approves; it does not define the registry. Sessions persist the conversation, not effects, and moving them across hosts is a file-shipping problem. Permissions are good product safety and a weak audit model: no actor/principal/spender split, no sealed record, no "text never grants power" as an engine rule. For a coding agent in one repo, the SDK is the better artifact today. For a fleet that must delete one session's content and keep its cost trail, it is not the design.

Pydantic AI is the closest on engineering hygiene and the furthest on owning the engine. V2 capabilities compose tools, hooks, and model settings. Structured output is a real contract. Durability is delegated to Temporal, DBOS, Prefect, and the rest, which is the right move if you already run a workflow engine. It also means recovery, approval, and idempotency are properties of the attached engine plus your activities, not of the agent record. Reviews of production use still put authorization and idempotency in application code. `agentic_core` argues, correctly, that two runs of one model call diverge, so the loop's writes are not activities you replay. Owning a mini workflow engine is the cost of that argument. Sitting on Temporal is the alternative, and the spec never says why an adopter should not do both: this record as source of truth, a workflow engine as the worker.

## Verdict

84 is a design score for a preliminary engine spec that already beats the public contracts of the adopted frameworks on recovery, fencing, spend, trust tiers, and deletion. It loses on executable completeness and on evidence. The deductions are the concurrent append, the shared budget ledger, the rule of two without a classifier, the absent threat model, and schemas deferred past the point where two teams would implement the same machine.

Ship the conformance kit against these rules, especially unsafe recovery, stale-writer refusal, and the budget hold, and the score moves. Leave them as principle boxes and this remains a strong architecture essay sitting next to systems people already run.
