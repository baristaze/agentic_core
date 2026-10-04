---
name: ops-loop-spend
description: "Reconcile one loop's model spend, in one environment, from the usage records the engine wrote, read through the operator plane's usage route with a read-only operator token: each turn's tokens by class and its cost, the context's growth across turns, cache-miss streaks, compaction churn, and the cost by agent kind, kind version, model role, and model, each with what to look at next. Takes the org, the session, and the loop. Reports; never writes, never fixes."
allowed-tools: Read, Bash(curl:*), Bash(jq:*), Bash(uv run acme-ops size:*)
---

# ops-loop-spend

One loop's bill, turn by turn, from what the engine recorded. Every
model call a provider billed left one usage record: its tokens by
class, its reference cost, and the labels of what served it, in every
storage mode (ADR 1014). A call whose usage was never reported whole
(a broken stream, a lost run) was settled at its whole hold: its record
is marked `settled_whole`, its cost is the hold's, and its tokens are
what a partial reply reported, else 0. This skill reads one loop's
records through the operator plane, checks them against the loop's
rollup, and says where the tokens went and what to look at next. It
changes nothing.

Read `../_shared/ops-preamble.md`, a path from this skill's folder,
before the first step: the env file and the token's refresh are there.

## Input

`--env local|staging|production --org <org_id> --session <session_id> --loop <loop_id> [--cursor <cursor>]`

The first four are required; ask for any that is missing. A loop is the
steps from one waking input to its outcome, and its id is that input's
step id. `ops-session-spend` lists a session's loops and runs this
skill over each of them. `--cursor` is the cursor of the page the
loop's first turn is on, which `ops-session-spend` names for a loop it
did not read turn by turn: the first read then starts at that page, so
the pages before it are not read again.

`local` reads the local API that the env file names. No cloud is
needed.

## Role and credential

The supporter's read of the operator plane, and nothing else: the env
file's operator token, whose permission is `read`. No cloud profile is
needed or used. Every read is a plain read of one of two routes at
`ACME_API_URL`: `/v1/admin/me`, and the usage route,
`/v1/admin/orgs/<org_id>/sessions/<session_id>/usage`.

Before step 2 sources the env file, run `uv run acme-ops size --env
<env>`, which refuses a file that holds the provisioner's token: then
stop, and give the person the line it printed. Any other answer, the
size or an error of its own, is not that refusal, and the run goes on.

A `write` operator is refused by this skill even when the file holds
its token (step 2). Never read the env file; a command that needs a
value sources it in the same command, as every block below does.
Never print a token. On a `401`, which a read prints as the error code
`not_authenticated`, the token has expired: stop, and name the refresh
the preamble gives.

## Procedure

A turn is one usage record: one model call the provider billed, in
`created_at` order. A record holds ids, counts, money, a duration, and
labels, and no content. Its `role` is `main` for the agent's own turns
and `summarizer` for a compaction, which runs inside the same loop; a
product may add roles of its own, which read the same way. A main turn
that follows a summarizer turn was sent after the window was compacted.

1. Run `uv run acme-ops size --env <env>`, as Role and credential
   states.
2. Read who the operator plane admitted:

   ```bash
   set -a; . ~/.config/acme/ops/<env>.env; set +a
   curl -s -H "Authorization: Bearer $ACME_OPERATOR_TOKEN" "$ACME_API_URL/v1/admin/me" \
     | jq '{operator_role, error: .error.code}'
   ```

   The run goes on only on `operator_role: read`. A `write` stops it:
   this skill holds the read token alone. An `error` stops it, and so
   does an answer that is empty or that `jq` cannot parse: `curl -s`
   prints nothing when the API is out of reach, and a parse error
   means the answer was not JSON. The report then says the operator
   plane was not read, and why. No read is made a second time.
3. Read the loop's records, 200 a page. With `--cursor`, the first
   read adds `&cursor=<next_cursor>` to this route, that cursor as
   `<next_cursor>`:

   ```bash
   set -a; . ~/.config/acme/ops/<env>.env; set +a
   curl -s -H "Authorization: Bearer $ACME_OPERATOR_TOKEN" "$ACME_API_URL/v1/admin/orgs/<org_id>/sessions/<session_id>/usage?limit=200" \
     | jq -c '{error: .error.code, next_cursor, has_more_loops, total_calls: .total.calls, rollup: ([.loops[]? | select(.loop_id == "<loop_id>") | .rollup][0]), turns: [.items[]? | select(.loop_id == "<loop_id>") | [.created_at, .agent_kind, .kind_version, .role, .model, .input_tokens, .cache_read_tokens, .cache_write_tokens, .output_tokens, .thinking_tokens, .cost_micros, .latency_ms, .settled_whole]]}'
   ```

   Each turn prints as one array, its fields in that order. `rollup` is
   the loop's sum over every record the session holds, whatever the
   page: `calls`, the five token classes, `cost_micros`, `unpriced`,
   `settled_whole`, and `latency_ms`; `total_calls` is the session's
   count of calls. When `next_cursor` is not null and the turns read so
   far are fewer than the rollup's `calls`, read the next page with it
   as `cursor`, through this `jq`:

   ```bash
   set -a; . ~/.config/acme/ops/<env>.env; set +a
   curl -s -H "Authorization: Bearer $ACME_OPERATOR_TOKEN" "$ACME_API_URL/v1/admin/orgs/<org_id>/sessions/<session_id>/usage?limit=200&cursor=<next_cursor>" \
     | jq -c '{error: .error.code, next_cursor, total_calls: .total.calls, turns: [.items[]? | select(.loop_id == "<loop_id>") | [.created_at, .agent_kind, .kind_version, .role, .model, .input_tokens, .cache_read_tokens, .cache_write_tokens, .output_tokens, .thinking_tokens, .cost_micros, .latency_ms, .settled_whole]]}'
   ```

   Read at most 5 pages, 1,000 records. After the fifth the read
   stops, cut, and the report says how many of the rollup's calls were
   not read; the rollup still totals them. A later page whose
   `total_calls` is above the first page's means the session wrote
   records while it was read: the read is live, and the rollup is the
   first page's.

   A `404` (`not_found`) ends the run: the org is unknown, or it holds
   no record of the session, which is also how another tenant's session
   reads. A null `rollup` ends the read at the first page: no later
   page is searched for the loop. With `has_more_loops` false, the
   session holds no record of this loop: the report says so, and names
   `ops-session-spend` for the session's loops. With `has_more_loops`
   true, the loop is past the thousand loops the route rolls up, so a
   thousand records or more come before its first, past the 5 pages
   this skill reads: the report says its turns and its rollup were not
   read, and why. An empty answer, or one `jq` cannot parse, ends the
   run as in step 2.

   When `ops-session-spend` runs this skill, it has made these reads
   once for the whole session: steps 1 to 3 are not run again, and
   step 4 starts from the turns and the rollup it hands over.
4. Reconcile, only when every record of the loop was read: the read
   ended at the rollup's `calls` or at a null `next_cursor`, neither cut
   at the fifth page nor live. Sum each token class, `cost_micros`, the
   count of turns, and the count of turns marked `settled_whole` over
   the turns read, and compare each with the rollup. Equal, the loop is
   reconciled. Different, it is a finding, with both figures, never
   adjusted to agree. A read that was cut or live, or a loop with no
   rollup, is not reconciled and is no finding: the report says which it
   was, with the figures read beside the rollup's. A cost in dollars is
   `cost_micros` divided by 1,000,000. A turn whose `cost_micros` is
   null had no price: the loop's cost is then a floor, and the report
   gives it as one, with the rollup's `unpriced` count. Beside it goes
   the rollup's `settled_whole` count: calls whose tokens are partial or
   unknown, each at its hold's cost.
5. Read the turns. A turn's prompt is its input, cache-read, and
   cache-write tokens together, and its cache share is its cache-read
   tokens over its prompt.

   - **Tokens per class and per turn.** One row per turn, with its
     prompt and its cache share.
   - **Context growth.** Each turn's prompt against the previous turn
     of the same role: the growth from the role's first turn to its
     last, and the largest single step. A step that more than doubles
     the prompt is a finding: something large entered the window
     between the two turns.
   - **Cache-miss streaks.** The engine moves a cache breakpoint along
     the window, so a turn reads from the cache what the previous turn
     of its role wrote there: that turn's cache-read and cache-write
     tokens together. A turn after its role's first turn in the loop
     whose cache read is under half of that is a miss, and two or more
     misses in a row are a streak. The cache share is not the measure: a
     turn that takes in more new tokens than its cached prefix holds has
     a low share and still read the whole prefix. Two kinds of turn are
     never misses, nor part of a streak. The first `main` turn after a
     `summarizer` turn reads a window the compaction rewrote, so its
     short cache read is the compaction's, read under compaction churn.
     A `summarizer` turn marks only its system prompt for the cache, so
     its share is low by design. A miss with cache-write tokens means
     the cached prefix was written again: the prefix changed, or its
     cache expired. A gap of more than five minutes since the role's
     previous turn points at the expiry; with no such gap, the prefix
     changed. A record is written when its call ends, so the gap runs
     from the previous turn's `created_at` to this turn's `created_at`
     minus its `latency_ms`. The record holds no prompt hash, so which
     part of the prefix changed is not recorded, and the report says so.
   - **Compaction churn.** Each `summarizer` turn is one compaction.
     What it saved is the prompt of the next main turn against that of
     the main turn before it. Two compactions with fewer than three
     main turns between them are churn: the window fills as fast as it
     is folded.
   - **Cost by agent kind, kind version, model role, and model.** The
     turns and the cost of each, and its share of the loop's cost.
   - **Repeated tool calls.** Not recorded: a usage record holds no
     tool name and no input. The report writes "not recorded", never a
     count. The calls are in the session's history, which is the
     tenant's content, and this skill does not read it.
6. Write the report. Each finding names what to look at next:

   - A miss that wrote the prefix again with no gap: the kind
     version's prompts and tool definitions, in the order they render,
     since anything that changes inside the cached prefix writes it
     again.
   - A miss that wrote the prefix again after a gap: the time between
     the turns, against the provider's cache lifetime.
   - A context that grows fast: the tool results and inputs the loop
     took in between the two turns.
   - Compaction churn: the window's size against what the loop adds
     each turn.
   - A mechanical task's turns on the `main` role: a model role of its
     own, which a cheaper model can fill.
   - An unpriced turn: the price table,
     `om/src/acme/om/budgets/impl/pricing.py`, has no row for its
     model.
   - A loop that does not reconcile: the figures, for a person to
     check against the records.

   The skill names no next skill of its own: the findings are a
   person's to act on.

## What it never does

- No request but a read: every `curl` reads `/v1/admin/me` or the
  usage route, with no method flag and no body.
- No token but the read one: the env file is sourced and never read,
  and a `write` operator stops the run.
- No content: a record holds none, and the skill reads nothing else of
  the session, its history least of all.
- No fix: it names what to look at, and changes no prompt, kind,
  price, or budget.
- No cloud: no `aws` command. The operator plane is the one source.
- No unbounded read: never more than 5 pages.

## Output

```markdown
# Loop spend: <env>, org <org_id>, session <session_id>, loop <loop_id>

**Credential.** the `read` operator token, admitted as `read`
**Reconciled.** <yes: <n> turns, $<cost> | no: the turns read <figures> against the rollup <figures> | not checked: <cut at 5 pages, <k> of <n> calls read | live, the session wrote records while it was read | no rollup>>

## Turns

| # | At | Kind | Version | Role | Model | Input | Cache read | Cache write | Output | Thinking | Prompt | Growth | Cache share | Cost ($) |
|---|----|------|---------|------|-------|-------|------------|-------------|--------|----------|--------|--------|-------------|----------|

## Totals

- Tokens: input <n>, cache read <n>, cache write <n>, output <n>, thinking <n>
- Cost: $<n>, <or: a floor, <k> turns had no price>; <j> calls settled whole, their tokens partial or unknown
- <kind> v<version>, <role>, <model>: <n> turns, $<n> (<p>%)

## Findings

- <what, the turns that show it, what to look at next; or none>

## Not recorded

- Repeated tool calls: a usage record holds no tool name or input.
- Which part of a prefix changed: a usage record holds no prompt hash.
- The full tokens of a call settled whole: its record holds what a partial reply reported, else 0.
```
