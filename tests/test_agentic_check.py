"""The scaffold's agentic-check against this repository: its catalog, its core set, and a clean scaffold.

The checker's own tests travel with it, under
`scaffold/acme_root/checkers/tests/`. These read what only this
repository has: the lens files, the spec, and the scaffold itself.
"""

import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("tomllib")

from acme.agentic_check import registry
from acme.agentic_check.cli import main, pinned_python
from acme.agentic_check.lenses import CORE, LENSES

REPO = Path(__file__).resolve().parent.parent
SCAFFOLD = REPO / "scaffold" / "acme_root"


def test_the_catalog_is_the_lens_files():
    """Every lens of `lenses/*.md`, with its severity, and nothing else."""
    found: dict[str, str] = {}
    for path in sorted((REPO / "lenses").glob("*.md")):
        lens = None
        for line in path.read_text(encoding="utf-8").splitlines():
            if m := re.match(r"^## ([A-Z]{3}-\d{2}) ", line):
                lens = m.group(1)
            elif (m := re.match(r"^\*\*Severity\.\*\* (\w+)", line)) and lens:
                found[lens] = m.group(1)
    assert found == LENSES


def test_the_core_lenses_are_those_whose_first_source_is_tagged_core():
    """The section a lens cites first states its rule, and its own tag decides, never a parent's."""
    import check_lenses

    known, tagged = check_lenses.sections(), check_lenses.tags()
    core: set[str] = set()
    for path in sorted((REPO / "lenses").glob("*.md")):
        if path.name == "README.md":
            continue
        text = path.read_text(encoding="utf-8")
        lines = text.split("\n")
        fenced = {n for n, code in enumerate(check_lenses.fenced_lines(text)) if code}
        for i, line in enumerate(lines):
            m = None if i in fenced else check_lenses.HEADING.match(line)
            if m:
                fields, _ = check_lenses.lens_fields(lines, i, fenced)
                parts = check_lenses.cited_parts(next(v for n, v, _ in fields if n == "Source"), known)
                if parts and tagged.get(parts[0]) == "core":
                    core.add(f"{m.group(1)}-{m.group(2)}")
    assert core == CORE
    assert {r.id for r in registry.rules()} - CORE == {"PRV-06"}


@pytest.mark.skipif(
    (pinned_python(SCAFFOLD) or (0, 0)) > sys.version_info[:2], reason="the scaffold pins a newer Python than this one"
)
def test_the_scaffold_is_clean(capsys):
    code = main(["--root", str(SCAFFOLD)])
    out, err = capsys.readouterr()
    assert (code, err) == (0, ""), out
