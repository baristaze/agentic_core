"""The rule registry: one registration per rule, keyed by its lens id.

A rule module under `acme.agentic_check.rules` registers each rule with the
`rule` decorator. `rules()` imports every module of that package, so a
rule is one new function in its group's module and nothing else to wire.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from collections.abc import Callable, Sequence

from acme.agentic_check.lenses import LENSES
from acme.agentic_check.model import COVERAGES, GROUPS, Check, Coverage, Rule

RULES: dict[str, Rule] = {}
"""The registered rules, by id."""

ID = re.compile(r"^([A-Z]{3})-(\d{2})$")
PREFIXES = {prefix: group for group, prefix in GROUPS.items()}


class RegistryError(Exception):
    """A registration that contradicts the lens catalog: a bug in a rule module."""


def make(
    id: str, check: Check, *, coverage: Coverage, summary: str, options: Sequence[str] = ()
) -> Rule:
    """A rule checked against the catalog: the lens exists, and the group and the severity are the lens's."""
    m = ID.match(id)
    if not m:
        raise RegistryError(f"rule id {id!r} is not PREFIX-NN")
    if m.group(1) not in PREFIXES:
        raise RegistryError(f"{id}: no lens group has the prefix {m.group(1)}")
    if id not in LENSES:
        raise RegistryError(f"{id}: no lens has this id")
    if coverage not in COVERAGES:
        raise RegistryError(f"{id}: coverage {coverage!r} is not full or partial")
    if not summary.strip():
        raise RegistryError(f"{id}: empty summary")
    return Rule(
        id=id,
        group=PREFIXES[m.group(1)],
        severity=LENSES[id],
        coverage=coverage,
        summary=summary,
        check=check,
        options=frozenset(options),
    )


def register(new: Rule) -> Rule:
    """Add a rule; a second rule for one lens is refused."""
    old = RULES.get(new.id)
    if old is not None and old.check is not new.check:
        raise RegistryError(f"{new.id} is registered twice")
    RULES[new.id] = new
    return new


def rule(
    id: str, *, coverage: Coverage, summary: str, options: Sequence[str] = ()
) -> Callable[[Check], Check]:
    """Register the decorated function as the rule that decides lens `id`.

    `coverage` is `full` when the rule decides the whole lens and
    `partial` when a review judges the rest. `summary` is one line for
    `--list`. `options` names every key the rule reads with
    `Project.option`, so a key it does not read exits 2 before the run.
    """

    def wrap(check: Check) -> Check:
        register(make(id, check, coverage=coverage, summary=summary, options=options))
        return check

    return wrap


def load() -> None:
    """Import every rule module, which registers its rules; modules starting with `_` are skipped."""
    package = importlib.import_module("acme.agentic_check.rules")
    for info in pkgutil.iter_modules(package.__path__):
        if not info.name.startswith("_"):
            importlib.import_module(f"acme.agentic_check.rules.{info.name}")


def order(r: Rule) -> tuple[int, str]:
    """Catalog order: the group's position, then the id."""
    return list(GROUPS).index(r.group), r.id


def rules() -> list[Rule]:
    """Every rule, in catalog order."""
    load()
    return sorted(RULES.values(), key=order)
