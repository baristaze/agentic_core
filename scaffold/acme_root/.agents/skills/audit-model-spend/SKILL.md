---
name: audit-model-spend
description: "Audit what an org's model calls spend, in one environment, from the usage records the engine wrote, read through the operator plane's usage route with a read-only operator token for the sessions named: the spend by agent kind, kind version, model role, and model, the prompt tokens read from a cache, written to one, and sent uncached, the hit rate, and what rebuilt caches cost; then a report with verdicts and proposed tickets. Never changes anything."
allowed-tools: Read, Write, Bash(curl:*), Bash(jq:*), Bash(uv run acme-ops size:*)
---

# audit-model-spend

Where the model money goes, and how much of it a cache could have
saved. Every model call a provider billed left a usage record: its
prompt tokens by how they were billed (read from a cache, written to
one, or sent uncached), its output and thinking, its reference cost,
and the agent kind, kind version, model role, and model that served it
(ADR 1014). A cache read is the cheap token; a cache write is a cache
rebuilt, paid above the uncached rate. A low hit rate on one kind
version is a prefix that keeps changing, which the bill shows only as
a total.

Read `../_shared/ops-preamble.md`, a path from this skill's folder,
before the first step: the env file and the token's refresh are there.

## Input

`--env local|staging|production --org <org_id> --session <session_id> [--session <session_id>]...`

`--env`, `--org`, and one `--session` at least are required; ask for
them when missing. The audit reads at most 20 sessions: the first 20
given, in the order given, and the report lists every one past the
twentieth as not read. The operator plane lists no org's sessions. The
ids come from the person, or from the org's events feed, where each
`agent_sessions.agent_session.created` event names a session as its
`target_id`; `ops-root-cause` reads that feed.

## Role and credential

Supporter, the operator plane's read alone: the env file's operator
token, whose permission is `read`. No cloud profile is needed or used.
Every read is a plain read of one of two routes at `ACME_API_URL`:
`/v1/admin/me`, and the usage route,
`/v1/admin/orgs/<org_id>/sessions/<session_id>/usage`. A record holds
ids, counts, money, a duration, and labels, and no content.

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

1. Run `uv run acme-ops size --env <env>`, as Role and credential
   states.
2. Read who the operator plane admitted:

   ```bash
   set -a; . ~/.config/acme/ops/<env>.env; set +a
   curl -s -H "Authorization: Bearer $ACME_OPERATOR_TOKEN" "$ACME_API_URL/v1/admin/me" \
     | jq '{operator_role, error: .error.code}'
   ```

   The run goes on only on `operator_role: read`. A `write` stops it,
   and so do an `error` and an answer that is empty or that `jq`
   cannot parse: the report then says the operator plane was not read,
   and why.
3. Read each session's records, 200 a page, summed by agent kind, kind
   version, model role, provider, and model, so a page prints one line
   per group and never a record:

   ```bash
   set -a; . ~/.config/acme/ops/<env>.env; set +a
   curl -s -H "Authorization: Bearer $ACME_OPERATOR_TOKEN" "$ACME_API_URL/v1/admin/orgs/<org_id>/sessions/<session_id>/usage?limit=200" \
     | jq -c '{error: .error.code, next_cursor, total, groups: ([.items[]?] | group_by([.agent_kind, .kind_version, .role, .provider, .model]) | map({group: (.[0] | [.agent_kind, .kind_version, .role, .provider, .model]), calls: length, input: (map(.input_tokens) | add), cache_read: (map(.cache_read_tokens) | add), cache_write: (map(.cache_write_tokens) | add), output: (map(.output_tokens) | add), thinking: (map(.thinking_tokens) | add), cost_micros: (map(.cost_micros // 0) | add), unpriced: (map(select(.cost_micros == null)) | length)}))}'
   ```

   When `next_cursor` is not null, read the next page of the same
   session with `&cursor=<next_cursor>` added to the route, through
   the same `jq`. Read at most 5 pages a session, 1,000 records. A
   session cut there is audited on the records read, and the report
   says how many of its `total` calls were not read. `total`, the
   session's rollup, covers every record whatever the page.

   A `404` (`not_found`) means the org is unknown, or it holds no
   record of that session, which is also how another tenant's session
   reads: the session is listed as not read, and the run goes on with
   the next. An empty answer, or one `jq` cannot parse, ends the run as
   in step 2, with the sessions already read audited. No read is made
   a second time.
4. Add the groups across the sessions read, page by page, and check
   each session's groups against its `total`: the calls and the cost
   agree, or the difference is a finding. Then compute, for each
   group: the prompt, input plus cache read plus cache write; the hit
   rate, `cache_read / prompt`; the share of the prompt written to a
   cache, `cache_write / prompt`; its share of the tokens; and its
   share of the spend. A cost in dollars is `cost_micros` divided by
   1,000,000. The rates of a cache write and an uncached input token
   are the price table's, in `om/src/acme/om/budgets/impl/pricing.py`
   (read it with `Read`), so the cost of rebuilt caches is each group's
   cache writes at the write rate's premium over the input rate, as an
   estimate the report labels so. Each kind version's total across its
   roles and models is reported beside its groups.
5. Judge each group. A hit rate under half is a finding, and so are
   cache writes above a tenth of the prompt, and a group whose spend
   share is far above its share of the tokens. A group with any
   unpriced call is a finding of its own: its cost is in no figure,
   and every total it is part of is a floor.
6. Write the report, with each finding's proposed ticket: what to
   change (the order of a kind version's prompt so its stable part
   comes first, a cache breakpoint, a tool definition that moves, a
   cheaper model role for a mechanical task, a missing price row), the
   evidence, and the effort. The audit proposes; it never fixes.

## What it never does

- Never writes to a shared database or to an environment: it reads the
  operator plane and writes its report under `~/Downloads/`.
- Never modifies a tracked file, never commits, never opens a pull
  request.
- No record printed whole: the `jq` keeps sums by group. A record
  holds no content, and the audit reads nothing else of a session.
- No token but the read one: the env file is sourced and never read,
  and a `write` operator stops the run.
- No cloud: no `aws` command. The operator plane is the one source.
- No unbounded read: never more than 20 sessions, and never more than
  5 pages of one.

## Output

`~/Downloads/acme_model_spend_<yyyy-mm-dd>.md`:

```markdown
# Model spend: <env>, org <org_id>, <n> sessions

**Answer.** <one sentence: where the money goes, and what a cache would save>

| Kind | Version | Role | Model | Calls | Spend ($) | Share | Hit rate | Cache writes | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| <kind> | <n> | <role> | <provider/model> | <n> | <n> | <p>% | <p>% | <p>% of prompt | ok / finding |

## Findings, by impact

1. <finding>: <evidence>; fix: <change>; effort <S/M/L>

## Proposed tickets

- <title>: <what, why, done-when>

## Not verified

- <each session not read, and why; each session cut at 5 pages>
```
