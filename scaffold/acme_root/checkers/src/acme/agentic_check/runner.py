"""Run the selected rules over a project and settle every finding against its exceptions.

Each rule yields its violations; every Python file is parsed, and one
that does not parse is a `PARSE` finding; then each finding is either
accepted by an exception in the configuration, with its ADR, or kept.
An exception that accepts no finding of a rule that ran is itself an
`IGNORE` finding, so an exception never outlives the code it excused.

Each rule runs on its own. A rule that raises is an `ERROR` finding
that names it, what it found before it raised is dropped, and the
other rules still run; the command exits 2, because a rule that did
not finish has not cleared the project.
"""

from __future__ import annotations

import sys
import traceback
from collections.abc import Sequence
from dataclasses import dataclass, field

from acme.agentic_check import __version__
from acme.agentic_check.config import PYPROJECT, ConfigError, glob_match
from acme.agentic_check.model import FRAMEWORK, Applied, Finding, Rule
from acme.agentic_check.project import Project

PARSE = "PARSE"
IGNORE = "IGNORE"
ERROR = "ERROR"


@dataclass
class Result:
    version: str
    root: str
    rules_run: list[Rule]
    files: int
    findings: list[Finding] = field(default_factory=list)
    applied: list[Applied] = field(default_factory=list)


def framework(rule: str, path: str, line: int, message: str) -> Finding:
    return Finding(
        rule=rule, group=FRAMEWORK, severity="high", path=path, line=line, col=1, message=message
    )


def run(project: Project, rules: Sequence[Rule], paths: Sequence[str] = ()) -> Result:
    """Run `rules`; `paths` limits what is reported, never what is read."""

    def reported(path: str) -> bool:
        return not paths or any(path == p or path.startswith(p.rstrip("/") + "/") for p in paths)

    raw: list[Finding] = []
    errors: list[Finding] = []
    failed: set[str] = set()
    for r in rules:
        try:
            found = list(r.check(project))
        except ConfigError:
            raise
        except Exception as raised:  # one rule never stops the others
            traceback.print_exc(file=sys.stderr)
            failed.add(r.id)
            detail = f": {raised}" if str(raised) else ""
            errors.append(
                framework(
                    ERROR,
                    PYPROJECT,
                    1,
                    f"{r.id} raised {type(raised).__name__}{detail}; its findings are missing",
                )
            )
        else:
            raw.extend(
                Finding(r.id, r.group, r.severity, v.path, v.line, v.col, v.message) for v in found
            )
    project.parse_all()
    for rel, (line, message) in sorted(project.parse_errors.items()):
        raw.append(framework(PARSE, rel, line, f"does not parse: {message}"))

    ran = {r.id for r in rules} - failed
    kept: list[Finding] = []
    applied: list[Applied] = []
    used: set[int] = set()
    for f in raw:
        matching = [
            i
            for i, e in enumerate(project.config.exceptions)
            if f.group != FRAMEWORK
            and e.rule == f.rule
            and e.path is not None
            and glob_match(e.path, f.path)
        ]
        if not matching:
            kept.append(f)
            continue
        used.update(matching)
        first = project.config.exceptions[matching[0]]
        applied.append(Applied(f.rule, f.path, f.line, first.adr, first.reason))
    for i, e in enumerate(project.config.exceptions):
        if e.rule in ran and i not in used:
            kept.append(
                framework(
                    IGNORE,
                    PYPROJECT,
                    1,
                    f"the exception for {e.rule} on {e.path} matches no finding",
                )
            )

    findings = [f for f in kept if reported(f.path)] + errors
    findings.sort(key=lambda f: (f.path, f.line, f.col, f.rule, f.message))
    applied.sort(key=lambda a: (a.path, a.line, a.rule))
    return Result(
        version=__version__,
        root=str(project.root),
        rules_run=list(rules),
        files=len(project.python_files),
        findings=findings,
        applied=applied,
    )
