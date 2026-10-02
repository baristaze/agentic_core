"""The models group: a call names a model role, never a model."""

from __future__ import annotations

import ast
from collections.abc import Iterator

from agentic_check.config import glob_match
from agentic_check.model import Violation
from agentic_check.project import Project, dotted, last, string
from agentic_check.registry import rule

FIELD = "model"
"""The name a model id travels under: a keyword, a dictionary key, a field, a parameter."""


def literal(node: ast.AST | None) -> str | None:
    """The model id a value writes: a non-empty string, or one given to `Field` as its default."""
    value = string(node)
    if value is None and isinstance(node, ast.Call) and last(dotted(node.func)) == "Field":
        given = [k.value for k in node.keywords if k.arg == "default"] or node.args[:1]
        value = string(given[0]) if given else None
    return value or None


def named_model(target: ast.AST) -> bool:
    return (isinstance(target, ast.Name) and target.id == FIELD) or (isinstance(target, ast.Attribute) and target.attr == FIELD)


@rule(
    "MOD-01",
    coverage="partial",
    options=("sites",),
    summary="No model id is written outside the price table and the fills: no string is a `model` argument, key, "
    "field, or default.",
)
def no_model_at_a_call_site(project: Project) -> Iterator[Violation]:
    """A string written as a model, anywhere in the source but the files
    `sites` names: a `model=` keyword, a `"model"` key of a dictionary,
    an assignment to a name, a field, or an attribute called `model`
    (a `Field(...)` default included), and a default of a parameter
    called `model`. Option `[tool.agentic-check.options.MOD-01]`:
    `sites`, globs of the files that may name a model (default the
    price table, `om/src/<pkg>/om/budgets/impl/pricing.py`, and the
    fills, `om/src/<pkg>/om/models/impl/resolver.py`). A model chosen
    per call from a variable, and a provider named at a call site, are
    judged."""
    base = f"om/src/{project.package.replace('.', '/')}/om"
    default = [f"{base}/budgets/impl/pricing.py", f"{base}/models/impl/resolver.py"]
    sites = project.option("MOD-01", "sites", default, {"sites"})
    for file, tree in project.trees():
        if any(glob_match(site, file.rel) for site in sites):
            continue
        for node in ast.walk(tree):
            found: list[tuple[ast.AST, str]] = []
            if isinstance(node, ast.Call):
                found += [(k.value, v) for k in node.keywords if k.arg == FIELD and (v := literal(k.value))]
            elif isinstance(node, ast.Dict):
                found += [(v, s) for k, v in zip(node.keys, node.values, strict=True) if string(k) == FIELD and (s := literal(v))]
            elif isinstance(node, ast.Assign | ast.AnnAssign):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = literal(node.value)
                if value and any(named_model(t) for t in targets):
                    found.append((node, value))
            elif isinstance(node, ast.arguments):
                positional = [*node.posonlyargs, *node.args]
                pairs = [*zip(positional[len(positional) - len(node.defaults) :], node.defaults, strict=True)]
                pairs += [(a, d) for a, d in zip(node.kwonlyargs, node.kw_defaults, strict=True) if d is not None]
                found += [(d, v) for a, d in pairs if a.arg == FIELD and (v := literal(d))]
            for at, value in found:
                yield Violation.at(
                    file.rel,
                    at,
                    f"names the model {value!r}; a call names a model role, and only the fills and the price table, "
                    "the files `sites` lists, name a model",
                )
