"""The steps group: a step is written once, and only the purge removes it."""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator

from acme.agentic_check.model import Violation
from acme.agentic_check.project import (
    Function,
    Project,
    SourceFile,
    docstrings,
    functions,
    is_under,
    string,
)
from acme.agentic_check.registry import rule

TABLE = "steps"
"""The history's table, as its table class names it in `__tablename__`."""
PURGE = "purge"
"""The prefix of the function names that make the purge: the one path that deletes a step."""
STATEMENTS = {"update", "delete"}
"""The statement builders that rewrite or remove rows, called on a table (`update(Steps)`) or as its method."""
UPSERT = "on_conflict_do_update"
"""An insert that rewrites the row it meets: an update, whatever builds it."""


def table_classes(project: Project) -> set[str]:
    """The full names of the classes under the OM whose `__tablename__` is the history's."""
    out: set[str] = set()
    for name, (file, cls) in project.classes.items():
        if not is_under(file.module, project.sub("om")):
            continue
        for node in cls.body:
            if (
                isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in node.targets)
                and string(node.value) == TABLE
            ):
                out.add(name)
    return out


def sql_pattern() -> re.Pattern[str]:
    """SQL text that updates the table, upserts into it, or deletes from it, in any schema and quoting."""
    table = rf'(?:ONLY\s+)?(?:"?\w+"?\.)?"?{TABLE}"?(?![\w."])'
    update = rf"\b(UPDATE)\s+{table}|\b(INSERT)\s+INTO\s+{table}[^;]*?\bDO\s+UPDATE\b"
    return re.compile(rf"{update}|\b(DELETE)\s+FROM\s+{table}", re.IGNORECASE | re.DOTALL)


def assigned(scope: ast.AST, name: str) -> list[ast.expr]:
    """Every value assigned to `name` directly in `scope`'s body, nested functions left out."""
    out: list[ast.expr] = []
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda):
            continue
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            out.append(node.value)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
        ):
            out.extend([node.value] if node.value is not None else [])
        stack.extend(ast.iter_child_nodes(node))
    return out


def names_table(
    project: Project, file: SourceFile, expr: ast.AST, scopes: list[ast.AST], tables: set[str]
) -> bool:
    """Whether an expression means the table: its class, its `__table__`, or a local name assigned either."""
    for node in ast.walk(expr):
        if isinstance(node, ast.Name | ast.Attribute) and project.full_name(file, node) in tables:
            return True
    if isinstance(expr, ast.Name):
        for scope in scopes:
            for value in assigned(scope, expr.id):
                if names_table(project, file, value, [], tables):
                    return True
    return False


def statement(project: Project, file: SourceFile, call: ast.Call) -> tuple[str, ast.AST] | None:
    """`(kind, target)` when a call builds an UPDATE or a DELETE: `update(t)`, `sqlalchemy.delete(t)`, `t.update()`, or
    `insert(t).on_conflict_do_update()`, an update."""
    func = call.func
    name = (
        func.attr
        if isinstance(func, ast.Attribute)
        else func.id
        if isinstance(func, ast.Name)
        else None
    )
    if name == UPSERT and isinstance(func, ast.Attribute):
        return "update", func.value
    if name not in STATEMENTS:
        return None
    full = project.full_name(file, func) or ""
    if full.startswith("sqlalchemy.") and call.args:
        return name, call.args[0]
    if isinstance(func, ast.Attribute) and not full.startswith("sqlalchemy"):
        return name, func.value
    return None


@rule(
    "STP-07",
    coverage="partial",
    summary="No statement updates the steps table, and only a purge deletes from it: "
    "no update(), and no delete() or SQL DELETE outside a purge_* function.",
)
def steps_are_written_once(project: Project) -> Iterator[Violation]:
    """The steps table is the class under the OM whose `__tablename__`
    is `steps`. Anywhere in the source, an SQLAlchemy `update()` of it
    (`update(Steps)`, `Steps.__table__.update()`, or a local name
    assigned either), and SQL text that updates it, are findings. A
    `delete()` of it, or SQL text that deletes from it, is a finding
    outside a function whose name starts with `purge`. A docstring is
    prose and never SQL. A row deleted through an ORM session, and a
    memory impl that rewrites what it holds, are judged."""
    tables = table_classes(project)
    sql = sql_pattern()
    for file, tree in project.trees():
        prose = docstrings(tree)
        owner: dict[int, Function] = {}
        for fn in functions(tree):
            for node in ast.walk(fn):
                owner[id(node)] = fn  # the innermost function wins: functions() runs outer to inner
        for node in ast.walk(tree):
            inner = owner.get(id(node))
            purge = inner is not None and inner.name.startswith(PURGE)
            if isinstance(node, ast.Call) and tables:
                built = statement(project, file, node)
                if built is None:
                    continue
                kind, target = built
                scopes: list[ast.AST] = [s for s in (inner, tree) if s is not None]
                if not names_table(project, file, target, scopes, tables):
                    continue
                if kind == "update":
                    yield Violation.at(
                        file.rel,
                        node,
                        "updates a step; a step is written once, and nothing rewrites it",
                    )
                elif not purge:
                    yield Violation.at(
                        file.rel,
                        node,
                        "deletes steps outside a purge; only the purge removes a history",
                    )
            elif (text := string(node)) is not None and id(node) not in prose:
                m = sql.search(text)
                if m and (m.group(1) or m.group(2)):
                    yield Violation.at(
                        file.rel, node, "holds SQL that updates a step; a step is written once"
                    )
                elif m and not purge:
                    yield Violation.at(
                        file.rel,
                        node,
                        "holds SQL that deletes steps outside a purge; only the purge removes a history",
                    )
