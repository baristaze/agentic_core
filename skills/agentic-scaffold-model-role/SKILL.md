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
  `--schema` names the schema of an answer in a shape, and only then is
  the fill's output a schema.

## Changed

The shape is the rows of `MAIN` and `SUMMARIZER`.

| File | Change |
|------|--------|
| `om/src/<name>/om/models/types/fill.py` | `<ROLE>`, a `ModelRole` beside `MAIN` and `SUMMARIZER`, with a docstring naming its task |
| `om/src/<name>/om/models/impl/resolver.py` | a `RoleFill` in `DEFAULT_TABLE`: the fill and its fallbacks |
| `om/src/<name>/om/budgets/impl/pricing.py` | a row in `LIST_PRICES` for each model the row names that has none, and the table's next version |
| `om/tests/unit/test_budgets.py` (a new row) | the model in `ADAPTER_MODELS`, and the table's version |
| `om/tests/unit/test_models.py` | the role's cases |
| `om/src/<name>/om/models/README.md` | the role, in the product's language, beside the agent's own turns and the summary |

## Procedure

1. A call names the role, never a model. A call site renders the role's
   request with `render_request` of the windows manager, `role=<ROLE>`:
   a side role reads a suffix of the history sized to its own fill. The
   session resolves the role among its roles (`resolve_fill_set` of the
   models manager), once, and keeps it.
2. Every model a fill or a fallback names has a price row of its own,
   or the resolver refuses it. A missing row is read from the
   provider's published price list, the page `LIST_PRICES` cites for
   that provider, on the day `date +%F` gives, with the provider's
   helper in that file, or a helper of its own in their shape for a
   provider that has none. A row's `as_of` is the day it was read, and a
   new reading is a new version of the table.
3. The tests hold: the role resolves from `DEFAULT_TABLE` to its fill
   and its fallbacks, in order, priced as `EVERY_DEFAULT` prices them in
   `om/tests/unit/test_models.py`; under an eligibility a fill misses,
   only the fills that meet it remain; and the list table prices every
   model the table names.

Then the gate, `make check`, as After writing in the conventions
runs it.

## Output

As `../_shared/scaffold-conventions.md` states.
