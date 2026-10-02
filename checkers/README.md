# agentic-check

`agentic-check` is the engine's static checker. Some of the engine's
lenses are syntactic: a string where a model role belongs, a statement
that rewrites a step, a dependency with a default. A parser decides
those in a second, offline, the same way every time. So the checker
takes what a program can decide without guessing, and the review skills
keep the judgment calls.

It is a sibling of the guideline's `arch-check`, in its shape.
`arch-check` holds the guideline's lenses and `agentic-check` the
engine's, and a project built on the engine's scaffold runs both in
`make check`. It reads the project's source with `ast` and never
imports it. It needs Python 3.11 or later and nothing else.

## Running it

A project pins the engine release it builds on:

```bash
uvx --python 3.14 --from "git+https://github.com/baristaze/agentic_core@v0.0.0#subdirectory=checkers" agentic-check
```

`--python` names the Python the project pins in `.python-version`. On
an older one the checker exits 2, rather than misread newer syntax.
From a checkout of this repository it runs without installing:

```bash
python3 checkers/agentic_check.py --root path/to/project
```

| Flag | Does |
|------|------|
| `PATHS...` | Report findings only under these paths. The whole project is still read. |
| `--root DIR` | The project root. The default is the nearest directory whose `pyproject.toml` has `[tool.agentic-check]`, else the current one. |
| `--group tools,steps` | Only the rules of these lens groups. |
| `--rule MOD-01,TOL-13` | Only these rules. |
| `--format text\|json` | `path:line:col: RULE message` lines, or one JSON document. |
| `--list` | Print every rule: id, group, coverage, severity, summary. |

## Exit status

- `0`: clean.
- `1`: findings.
- `2`: a configuration or usage error, or a rule that raises. A rule
  that fails is an `ERROR` finding that names it, and every other rule
  still runs.

A file that does not parse is a `PARSE` finding, never a crash.

## Rules and lenses

A rule's id is the id of the lens it decides, and its severity is the
lens's. Its coverage is `full` when it decides the whole lens, or
`partial` when it decides a named part and a review judges the rest.
The lens's **Check** line says which, and `make lenses` holds the line
and the rule to each other. A rule ships only when it is deterministic
and rarely wrong on a tree in the scaffold's shape. A rule that would
have to guess stays with the review.

## Configuration

The configuration is `[tool.agentic-check]` in the root
`pyproject.toml`. Every key defaults to the scaffold's layout, so a
project built on it names only its package:

```toml
[tool.agentic-check]
package = "acme"
# src = ["om/src", "infra/src", "integrations/src", "services/*/src", ...]
# exclude = ["**/generated/**"]
```

A rule that needs the project's own names reads them as options, one
table per rule id, each defaulting to the scaffold's. A key the rule
does not read exits 2:

```toml
[tool.agentic-check.options.MOD-01]
sites = ["om/src/acme/om/budgets/impl/pricing.py", "om/src/acme/om/models/impl/*.py"]

[tool.agentic-check.options.PRV-06]
modules = ["om.agent_sessions", "om.steps", "om.tools"]
```

## Exceptions

Turning a rule off, or letting one file break it, is a deviation from
the engine's spec, and its ADR comes first. Each entry's `adr` names a
Markdown file under `docs/adr/` that exists:

```toml
[[tool.agentic-check.disable]]
rule = "TOL-01"
adr = "docs/adr/2001-tools-declared-by-a-catalog.md"
reason = "one line"

[[tool.agentic-check.exception]]
rule = "MOD-01"
path = "om/src/acme/om/models/impl/catalog.py"
adr = "docs/adr/2002-a-catalog-of-models.md"
reason = "one line"
```

An exception that matches nothing is itself a finding, so an exception
never outlives the code it excused.

## Adding a rule

A rule is one function in `src/agentic_check/rules/<group>.py`,
registered with `@rule("<LENS-ID>", coverage=..., summary=...)`. The
loader imports every module there, so there is nothing else to wire.
The registration refuses an id no lens has, and the group and the
severity come from the lens. `Project` holds the parsed tree and
resolves a name to the module that defines it.

A rule comes with tests in `tests/test_agentic_check_rules.py`: a tree
that breaks it and the clean one, built with
`tests/agentic_check_fixtures.py`. Its lens's Check line names the
checker in the same change. Before it ships, it runs clean on the
scaffold, which `tests/test_agentic_check_framework.py` holds.
