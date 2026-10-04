---
name: ops-session-spend
description: "Reconcile one session's model spend, in one environment, loop by loop, from the usage records the engine wrote: the session's rollup and each loop's, read once through the operator plane's usage route with a read-only operator token, then ops-loop-spend's reading over each loop, at most ten. Takes the org and the session. Reports; never writes, never fixes."
allowed-tools: Read, Bash(curl:*), Bash(jq:*), Bash(uv run acme-ops size:*)
---

# ops-session-spend

A session's bill is the sum of its loops. This skill reads the
session's usage once, checks the loops against the session's total,
and runs `ops-loop-spend`'s reading over each loop, so a loop reads the
same alone or inside its session. It changes nothing.

Read `../_shared/ops-preamble.md` and `../ops-loop-spend/SKILL.md`,
paths from this skill's folder as `realpath` resolves it, before the
first step. The first holds the env file and the token's refresh; the
second holds the reading this skill runs over each loop.

## Input

`--env local|staging|production --org <org_id> --session <session_id>`

All three are required; ask for any that is missing. `local` reads the
local API that the env file names. No cloud is needed.

## Role and credential

The supporter's read of the operator plane, and nothing else: the env
file's operator token, whose permission is `read`. No cloud profile is
needed or used. Every read is a plain read of one of two routes at
`ACME_API_URL`: `/v1/admin/me`, and the usage route,
`/v1/admin/orgs/<org_id>/sessions/<session_id>/usage`.

Before step 1 sources the env file, run `uv run acme-ops size --env
<env>`, which refuses a file that holds the provisioner's token: then
stop, and give the person the line it printed. Any other answer, the
size or an error of its own, is not that refusal, and the run goes on.

A `write` operator is refused by this skill even when the file holds
its token. Never read the env file; a command that needs a value
sources it in the same command, as every block below does. Never print
a token. On a `401`, which a read prints as the error code
`not_authenticated`, the token has expired: stop, and name the refresh
the preamble gives.

## Procedure

1. Run steps 1 and 2 of `ops-loop-spend`: the refusal of a file that
   holds the provisioner's token, and the read of who the operator
   plane admitted, which goes on only on `operator_role: read`.
2. Read the session's usage, 200 records a page. The first page
   carries the rollups:

   ```bash
   set -a; . ~/.config/acme/ops/<env>.env; set +a
   curl -s -H "Authorization: Bearer $ACME_OPERATOR_TOKEN" "$ACME_API_URL/v1/admin/orgs/<org_id>/sessions/<session_id>/usage?limit=200" \
     | jq -c '{error: .error.code, next_cursor, has_more_loops, total, loops, turns: [.items[]? | [.loop_id, .created_at, .agent_kind, .kind_version, .role, .model, .input_tokens, .cache_read_tokens, .cache_write_tokens, .output_tokens, .thinking_tokens, .cost_micros, .latency_ms]]}'
   ```

   `total` is the session's rollup; `loops` holds one `{loop_id,
   rollup}` per loop, in the order each loop first called, up to a
   thousand, and `has_more_loops` says the session ran more. Both
   cover every record, whatever the page. Each turn prints as one
   array, its loop's id first, then the fields `ops-loop-spend` reads,
   in its order. When `next_cursor` is not null, read the next page
   with it as `cursor`; the rollups repeat on every page, so this `jq`
   leaves them out:

   ```bash
   set -a; . ~/.config/acme/ops/<env>.env; set +a
   curl -s -H "Authorization: Bearer $ACME_OPERATOR_TOKEN" "$ACME_API_URL/v1/admin/orgs/<org_id>/sessions/<session_id>/usage?limit=200&cursor=<next_cursor>" \
     | jq -c '{error: .error.code, next_cursor, turns: [.items[]? | [.loop_id, .created_at, .agent_kind, .kind_version, .role, .model, .input_tokens, .cache_read_tokens, .cache_write_tokens, .output_tokens, .thinking_tokens, .cost_micros, .latency_ms]]}'
   ```

   Read at most 5 pages, 1,000 records. After the fifth the read
   stops, and the report says how many of the session's calls were not
   read; the rollups still total them.

   A `404` (`not_found`) ends the run: the org is unknown, or it holds
   no record of the session, which is also how another tenant's session
   reads. An empty answer, or one `jq` cannot parse, ends it as
   `ops-loop-spend` step 2 says. No read is made a second time.
3. Reconcile the session. The loops' rollups sum to `total`, each
   figure, unless `has_more_loops` is true; the turns read sum to
   `total` when every page was read. A figure that does not agree is a
   finding, with both figures, never adjusted. A `total` with any
   `unpriced` call is a floor, and the report gives it as one.
4. For each loop in `loops`, in order, run steps 4 to 6 of
   `ops-loop-spend` for that loop: on the turns step 2 read whose loop
   id is its id, and on its rollup from `loops`. Its records are not
   read again. Run it over at most 10 loops: the first ten in `loops`.
   Every loop past the tenth gets its rollup's line alone, its calls
   and its cost, with no reading of its turns, and the report names
   `ops-loop-spend` with that loop's id for a second run.
5. Read across the loops:

   - The loop that cost the most, and its share of the session's cost.
   - A loop whose first `main` turn wrote the prefix again after the
     session's first loop: the cache did not last from one loop to the
     next, so each waking input pays the prefix again. Its gap since
     the loop before says whether the cache expired between them.
   - The cost by agent kind, kind version, model role, and model,
     across the session.
6. Write the report: the session's totals, a line per loop, then each
   loop's reading as `ops-loop-spend` writes it, without its own title
   and credential lines. The skill names no next skill of its own
   beyond the second runs of step 4: the findings are a person's to act
   on.

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
- No unbounded read: never more than 5 pages, and never more than 10
  loops read turn by turn.

## Output

```markdown
# Session spend: <env>, org <org_id>, session <session_id>

**Credential.** the `read` operator token, admitted as `read`
**Reconciled.** <yes | no: <figure>, the loops <n> against the total <n>>
**Total.** <calls> calls, $<cost> <or: a floor, <k> calls had no price>; input <n>, cache read <n>, cache write <n>, output <n>, thinking <n>

## Loops

| Loop | First call | Turns | Input | Cache read | Cache write | Output | Thinking | Cost ($) | Share |
|------|------------|-------|-------|------------|-------------|--------|----------|----------|-------|

## By kind, version, role, and model

- <kind> v<version>, <role>, <model>: <n> turns, $<n> (<p>%)

## Findings

- <what, the loop and turns that show it, what to look at next; or none>

## Loop <loop_id>

<ops-loop-spend's Reconciled line, Turns, Totals, and Findings, for each loop read>

## Not recorded

- Repeated tool calls: a usage record holds no tool name or input.
- Which part of a prefix changed: a usage record holds no prompt hash.
```
