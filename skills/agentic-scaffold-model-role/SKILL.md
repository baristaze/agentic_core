---
name: agentic-scaffold-model-role
description: "Add a model role: the task a call names, its fill and its declared fallbacks in the resolver's table, a price row for every model they name, and the tests, in the shape of the engine's main and summarizer roles. Python."
allowed-tools: Read, Grep, Glob, Write, Edit, WebFetch, Bash(make check), Bash(uv run:*), Bash(git status:*), Bash(date:*)
---

# agentic-scaffold-model-role

A path that starts with `../` is read from this skill's folder as
`realpath` resolves it.
Conventions: `../_shared/scaffold-conventions.md`.
Sections of `../../agentic_core_spec.md`: Models (Roles and Fills, Fill
Sets and Switches), Context (Windows), Bounds and Budgets (Usage and
Cost).
Lenses: `../../lenses/models.md`.

## Input

`<role> --fill <provider>/<model> [--fallback <provider>/<model> ...] [--effort <effort>] [--max-output <tokens>] [--window <tokens>] [--schema <name>]`,
and the task the role does, in the arguments or in the conversation.

Example: `title --fill anthropic/claude-haiku-4-5 --fallback openai/gpt-6-luna --max-output 200`,
for "names a session in a few words".

- `<role>` names the task, never a model or a provider, as `ModelRole`
  in `om/src/<name>/om/models/types/fill.py` holds it. `<ROLE>` is its
  constant in upper snake case.
- `--fill` and each `--fallback` name a `ProviderName` and a model.
  Fallbacks are in the order a fallback takes them. A provider the tree
  has no adapter for is added first with
  `agentic-scaffold-provider-adapter`.
- `--max-output` stays below `--window`, the model's context window.
  `--effort`, `--max-output`, and `--window` hold for the fill and each
  fallback. What they leave out, a fill takes from a fill of the same
  model in `DEFAULT_TABLE`; with none, from the provider's documented
  limits for that model.
  `--schema` names the schema of an answer in a shape, and only then is
  the fill's output a schema.

## Changed

The shape is the rows of `MAIN` and `SUMMARIZER`.

| File | Change |
|------|--------|
| `om/src/<name>/om/models/types/fill.py` | `<ROLE>`, a `ModelRole` beside `MAIN` and `SUMMARIZER`, with a docstring naming its task |
| `om/src/<name>/om/models/impl/resolver.py` | a `RoleFill` in `DEFAULT_TABLE`: the fill and its fallbacks; the table's docstring names the roles it holds |
| `om/src/<name>/om/budgets/impl/pricing.py` (a model with no row) | its row in `LIST_PRICES`, and the table's next version |
| `om/tests/unit/test_budgets.py` (a new row) | the model in `ADAPTER_MODELS`, and the table's version |
| `om/tests/unit/test_models.py` | the role's cases |
| `om/src/<name>/om/models/README.md` | the role, in the product's language, beside the agent's own turns and the summary |

## Procedure

1. A call names the role, never a model. Its call site is the
   product's code for the task, which this skill does not write: it
   renders the role's request with `render_request` of the windows manager, `role=<ROLE>`:
   a side role reads a suffix of the history sized to its own fill. The
   session resolves the role among its roles (`resolve_fill_set` of the
   models manager), once, and keeps it.
2. Every model a fill or a fallback names has a price row of its own,
   or the resolver refuses it. A missing row is read from the
   provider's published price list, the page `LIST_PRICES` cites for
   that provider, on the day `date +%F` gives, with the provider's
   helper in that file. For a provider with no page cited yet, read the
   provider's own published pricing page, cite it in the `LIST_PRICES`
   docstring, and write its helper in the shape of the others. A model
   no published page lists stops the run, naming the missing price: a
   price is never estimated, since a guess under-reserves the gate's
   hold. A row's `as_of` is the day it was read: the helper takes it as
   an argument that defaults to `READ`, the new row passes today's, and
   the rows read before keep theirs. The table's next version is
   today's date; when it already carries today's, it is that date with
   `.2`, then `.3`.
3. The tests, in `om/tests/unit/test_models.py`, hold: the role
   resolves from `DEFAULT_TABLE` to its fill and its fallbacks, in
   order, priced as `EVERY_DEFAULT` prices them; and the list table,
   `LIST_PRICES`, prices every model the table names.

Then the gate, `make check`, as After writing in the conventions
runs it.

## Output

As `../_shared/scaffold-conventions.md` states.
