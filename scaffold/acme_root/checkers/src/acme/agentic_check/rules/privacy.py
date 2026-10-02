"""The privacy group: every dependency is required, and a root wires a null object where none is given."""

from __future__ import annotations

import ast
from collections.abc import Iterator

from acme.agentic_check.model import Violation
from acme.agentic_check.project import Project, SourceFile, dotted, is_under, last, names_in
from acme.agentic_check.registry import rule

ENGINE = [
    "om.agent_sessions",
    "om.steps",
    "om.windows",
    "om.models",
    "om.tools",
    "om.budgets",
    "om.agents",
    "om.attribution",
    "om.privacy",
    "infra.transports",
    "infra.workspaces",
    "infra.keys",
    "integrations.model_providers",
]
"""The engine's code, relative to the package: its namespaces, the capabilities under infra,
and the provider adapters."""


def interface(annotation: ast.AST | None) -> str | None:
    """The interface an annotation names, by the guideline's suffix, else None."""
    return next((n for n in names_in(annotation) if n.endswith("Interface")), None)


def optional(annotation: ast.AST | None) -> bool:
    """Whether an annotation admits None: `X | None`, `Optional[X]`, or `Union[X, None]`."""
    names = names_in(annotation)
    return "Optional" in names or any(
        isinstance(n, ast.Constant) and n.value is None for n in ast.walk(annotation or ast.Pass())
    )


def constructor_parameters(
    cls: ast.ClassDef,
) -> Iterator[tuple[ast.AST, str, ast.AST | None, bool]]:
    """(node, name, annotation, has a default) for each parameter of `__init__`, and each field of a dataclass."""
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name == "__init__":
            a = node.args
            positional = [*a.posonlyargs, *a.args][1:]
            first_default = len(positional) - len(a.defaults)
            for i, p in enumerate(positional):
                yield p, p.arg, p.annotation, i >= first_default
            for p, d in zip(a.kwonlyargs, a.kw_defaults, strict=True):
                yield p, p.arg, p.annotation, d is not None
    if any(last(dotted(d)) == "dataclass" for d in cls.decorator_list):
        for node in cls.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                yield node, node.target.id, node.annotation, node.value is not None


def engine_files(project: Project, modules: list[str]) -> list[SourceFile]:
    """The engine's modules, each OM namespace's storage left out: a storage impl has the guideline's shape."""
    prefixes = [project.sub(m) for m in modules]
    storage = [f"{p}.storage" for p in prefixes if is_under(p, project.sub("om"))]
    return [
        f
        for f in project.modules_under(*prefixes)
        if not any(is_under(f.module, s) for s in storage)
    ]


@rule(
    "PRV-06",
    coverage="partial",
    options=("modules",),
    summary="No __init__ parameter or dataclass field in the engine's modules, storage left out, takes an interface "
    "with a default or as optional: a root wires a null object.",
)
def every_dependency_is_wired(project: Project) -> Iterator[Violation]:
    """In the engine's modules, no `__init__` parameter and no dataclass
    field whose annotation names an `*Interface` has a default or admits
    `None`. A dependency nobody provided is a null object a root wires,
    never a constructor default, so no engine code asks whether one is
    there. Option `[tool.agentic-check.options.PRV-06]`: `modules`, the
    engine's packages relative to the package (default its nine OM
    namespaces, `infra.transports`, `infra.workspaces`, `infra.keys`,
    and `integrations.model_providers`); each OM namespace's `storage`
    is left out, since a memory storage impl has the guideline's shape.
    A root module, which wires the null objects, is outside them. Which
    interfaces have a null object, and a branch on a missing dependency,
    are judged."""
    modules = project.option("PRV-06", "modules", ENGINE, {"modules"})
    for file in engine_files(project, modules):
        tree = project.tree(file)
        if tree is None:
            continue
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            for node, name, annotation, has_default in constructor_parameters(cls):
                kind = interface(annotation)
                if kind is None:
                    continue
                if has_default:
                    yield Violation.at(
                        file.rel,
                        node,
                        f"{cls.name} takes {name}: {kind} with a default; a root wires every dependency",
                    )
                elif optional(annotation):
                    yield Violation.at(
                        file.rel,
                        node,
                        f"{cls.name} takes {name}: {kind} as optional; a root wires a null object instead",
                    )
