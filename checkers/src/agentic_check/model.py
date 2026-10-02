"""The values every part of agentic-check shares: a rule, a violation, a finding.

A rule is a function over a `Project` that yields `Violation`s. Its
registration (`agentic_check.registry.rule`) carries what the lens says
about it: the id, the group, the severity, and how much of the lens the
rule decides. The runner turns each violation into a `Finding` stamped
with those fields, so a rule never repeats them.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from agentic_check.project import Project

Severity = Literal["high", "medium", "low"]
Coverage = Literal["full", "partial"]

SEVERITIES: tuple[Severity, ...] = ("high", "medium", "low")
COVERAGES: tuple[Coverage, ...] = ("full", "partial")

GROUPS: dict[str, str] = {
    "steps": "STP",
    "windows": "WIN",
    "models": "MOD",
    "tools": "TOL",
    "live": "LIV",
    "trust": "TRU",
    "agents": "AGT",
    "bounds": "BND",
    "privacy": "PRV",
}
"""Each lens group and the id prefix of its lenses, in the order of `lenses/README.md`."""

FRAMEWORK = "framework"
"""The group of the findings agentic-check raises about itself: `PARSE`, `IGNORE`, and `ERROR`."""


@dataclass(frozen=True)
class Violation:
    """One breach a rule reports: a file relative to the root, a position, one sentence."""

    path: str
    line: int
    col: int
    message: str

    @classmethod
    def at(cls, path: str, node: ast.AST | None, message: str) -> Violation:
        """A violation at an `ast` node, columns counted from 1; no node means line 1."""
        line = getattr(node, "lineno", 1) if node is not None else 1
        col = getattr(node, "col_offset", 0) + 1 if node is not None else 1
        return cls(path, line, col, message)


Check = Callable[["Project"], Iterable[Violation]]


@dataclass(frozen=True)
class Rule:
    """A registered rule. `id` is the id of the lens it decides.

    `options` is every key the rule reads under
    `[tool.agentic-check.options.<id>]`; any other key there exits 2.
    """

    id: str
    group: str
    severity: Severity
    coverage: Coverage
    summary: str
    check: Check
    options: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Finding:
    """A violation stamped with the rule that raised it, as the report prints it."""

    rule: str
    group: str
    severity: str
    path: str
    line: int
    col: int
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "group": self.group,
            "severity": self.severity,
            "path": self.path,
            "line": self.line,
            "col": self.col,
            "message": self.message,
        }


@dataclass(frozen=True)
class Applied:
    """A finding an exception in the configuration accepted, with the ADR that records it."""

    rule: str
    path: str
    line: int
    adr: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "path": self.path, "line": self.line, "adr": self.adr, "reason": self.reason}
