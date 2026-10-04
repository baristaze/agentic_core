# ADR 1014: A model call's usage is recorded apart from its history, with no content

**Status**: accepted (2026-10-04)

## Context

The spec records two figures on every model call: native usage, tokens
by class, and reference cost. The response step's shape carries the
usage by class, and the ledger's settlement keeps the reference cost and
one token total
([ADR 1006](1006-a-hold-is-held-before-the-call-and-written-once.md)).

A memory-only session may keep no shape at rest. Its steps then live
only while a runtime holds them, and its usage by class goes when the
runtime does. The ledger alone cannot reconcile a bill: it holds no
cache classes, no model, and no latency. Forbidding a memory-only
session that keeps no shape would make a tenant that accepts nothing at
rest keep its shape anyway.

## Decision

**Every billed call leaves a usage record.** It is one row of
`activity.usage_records`, in the `budgets` namespace beside the ledger.
It holds the call's hold, its session, the session's tree, its loop,
and the step that answered it; the agent kind and its version, the
model role, the provider, and the model; input, cache-read, cache-write,
output, and thinking tokens; the reference cost, `cost_micros`, at the
price the hold read, or null when no price applied; and the provider's
latency. The call gate writes it
when it settles a call the provider billed, once per hold: a second
write of the same hold lands nothing.

**A call settled whole is recorded at its hold, and marked.** A stream
that broke after it began, a call its run abandoned mid-stream, and a
request a lost run left unanswered are settled at their whole hold. Each
record says `settled_whole`: its `cost_micros` is the hold, as the
ledger settled it, and its tokens are what a partial reply reported,
else none. A lost run's call was never held by the gate that settles
it, so the loop names it from its request step and its session; its
latency is unknown, and is 0. A rollup counts such calls apart, so a
reading's cost matches the ledger's and says how much of it is a worst
case.

**A record never fails its call.** The record is written once the ledger
has settled. A write that fails is logged, and the call, its reply, and
its loop go on: the ledger counts the call, and only the reading lacks
it.

**It holds no content.** Its columns are ids, counts, money, a duration,
and labels the product or its catalog defines. No title, prompt, reply,
tool input or output, attachment, or word a person typed reaches it. So
it is written in every storage mode, and `keep_shape` decides only
whether a memory-only session's shape is kept. A test lists the
columns, so a new one is a decision that it holds no content.

**It is billing data, kept as the ledger is.** It is written once: the
serving logins hold SELECT and INSERT, and the purge login holds
nothing. The purge that erases a session's history
([ADR 1010](1010-a-history-is-purged-by-a-login-of-its-own.md)) leaves
its records, and so does a deleted tenant's purge. The sweep's ledger
step never counts them, so they never keep a deleted tenant from being
marked purged.

**The operator plane reads a session's usage.**
`GET /v1/admin/orgs/{org_id}/sessions/{session_id}/usage` takes an
operator token with the read permission; a tenant's token is no
operator's, and is refused with 401. It answers 404 `not_found` for an
unknown org and for a session the org holds no record of, which is how
another tenant's session reads. Each read logs the operator and the
org, and none of the figures. The answer, `SessionUsageView`:

- `session_id`.
- `items`: a page of records, oldest first, each with `id`,
  `created_at`, `hold_id`, `session_id`, `tree_id`, `loop_id`,
  `step_id`, `agent_kind`, `kind_version`, `role`, `provider`, `model`,
  `input_tokens`, `cache_read_tokens`, `cache_write_tokens`,
  `output_tokens`, `thinking_tokens`, `cost_micros`, `latency_ms`, and
  `settled_whole`.
- `next_cursor`: the next page's `cursor`, or null.
- `loops`: one `{loop_id, rollup}` per loop, in the order each loop
  first called, up to a thousand, and `has_more_loops` past them.
- `total`: the session's rollup.

A rollup holds `calls`, the five token classes, `cost_micros`,
`unpriced` (the calls no price applied to, whose cost no figure holds),
`settled_whole` (the calls counted at their whole hold), and
`latency_ms`. The rollups cover every record, whatever the page.

**The tenant sees a step's usage.** Each model response in the tenant's
history carries its usage by class, where the session keeps its shape.

## Consequences

- A call's cost survives every storage mode, and a bill reconciles per
  call, per loop, and per session. A loop's, a role's, or a kind
  version's spend is a query over the records, even once the history
  is gone.
- A rollup's cost matches the ledger's for the same priced calls. Where
  `settled_whole` is above 0, part of it is a hold's worst case, not a
  usage.
- A rollup with an unpriced call is a floor, not a total.
- A record that failed to land leaves its call out of the reading, and
  the log says which hold.
- Records accumulate with no delete. Their retention is decided with
  the ledger's.
- The gate keeps what its hold read in process until the call settles.
  A settlement in another process writes a record only when its caller
  names the call, as the loop does for a lost run's request; otherwise
  it is logged.
