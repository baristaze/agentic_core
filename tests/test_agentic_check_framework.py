"""checkers/src/agentic_check: the catalog, the configuration, exceptions, reports, and exit codes.

The base tree is clean under every rule, so a finding here is one a
test planted: MOD-01's, a model named where no model is named.
"""

import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("tomllib")

from agentic_check import __version__, registry
from agentic_check.cli import pinned_python
from agentic_check.lenses import LENSES
from agentic_check_fixtures import ADR, OM, PYPROJECT, check, check_json, write, write_project

REPO = Path(__file__).resolve().parent.parent
SCAFFOLD = REPO / "scaffold" / "acme_root"
RULES = [r.id for r in registry.rules()]
BAD = {f"{OM}/agents/kinds.py": "MAIN = dict(model='claude-y')\n"}


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


def test_every_rule_decides_a_lens_of_its_group_at_its_severity():
    for r in registry.rules():
        assert r.severity == LENSES[r.id]
        assert r.coverage in ("full", "partial")
    with pytest.raises(registry.RegistryError, match="no lens has this id"):
        registry.make("STP-99", lambda p: [], coverage="full", summary="x")
    with pytest.raises(registry.RegistryError, match="no lens group has the prefix"):
        registry.make("CON-01", lambda p: [], coverage="full", summary="x")


def test_a_clean_tree_exits_0(tmp_path):
    write_project(tmp_path)
    code, out, err = check(tmp_path)
    assert (code, err) == (0, "")
    assert out.startswith(f"agentic-check ok: {len(RULES)} rule(s) over ")


def test_a_finding_exits_1_with_a_text_line(tmp_path):
    write_project(tmp_path, BAD)
    code, out, _ = check(tmp_path)
    assert code == 1
    assert out.splitlines()[0].startswith(f"{OM}/agents/kinds.py:1:19: MOD-01 names the model 'claude-y'")
    assert f"1 finding(s) from {len(RULES)} rule(s)" in out


def test_the_json_report(tmp_path):
    write_project(tmp_path, BAD)
    code, report = check_json(tmp_path)
    assert code == 1
    assert list(report) == ["version", "root", "rules_run", "findings", "exceptions_applied"]
    assert report["version"] == __version__
    assert [r["id"] for r in report["rules_run"]] == RULES
    assert report["findings"][0] | {"message": ""} == {
        "rule": "MOD-01",
        "group": "models",
        "severity": "medium",
        "path": f"{OM}/agents/kinds.py",
        "line": 1,
        "col": 19,
        "message": "",
    }


def test_list_group_rule_and_paths(tmp_path):
    write_project(tmp_path, BAD)
    code, out, _ = check(tmp_path, "--list")
    assert code == 0
    assert [line.split()[0] for line in out.splitlines()] == RULES
    assert check(tmp_path, "--group", "tools")[0] == 0
    assert check(tmp_path, "--rule", "MOD-01")[0] == 1
    assert check(tmp_path, f"{tmp_path}/infra")[0] == 0
    assert check(tmp_path, "--group", "nope")[0] == 2
    assert check(tmp_path, "--rule", "STP-01")[0] == 2


def test_an_exception_with_its_adr_accepts_a_finding(tmp_path):
    entry = (
        f'\n[[tool.agentic-check.exception]]\nrule = "MOD-01"\npath = "{OM}/agents/*.py"\nadr = "{ADR}"\nreason = "a catalog"\n'
    )
    write_project(tmp_path, BAD, pyproject=PYPROJECT + entry)
    code, report = check_json(tmp_path)
    assert (code, report["findings"]) == (0, [])
    assert report["exceptions_applied"] == [
        {"rule": "MOD-01", "path": f"{OM}/agents/kinds.py", "line": 1, "adr": ADR, "reason": "a catalog"}
    ]


def test_an_exception_that_matches_nothing_is_a_finding(tmp_path):
    entry = f'\n[[tool.agentic-check.exception]]\nrule = "MOD-01"\npath = "{OM}/agents/*.py"\nadr = "{ADR}"\nreason = "gone"\n'
    write_project(tmp_path, pyproject=PYPROJECT + entry)
    code, report = check_json(tmp_path)
    assert code == 1
    assert [(f["rule"], f["group"]) for f in report["findings"]] == [("IGNORE", "framework")]


def test_a_disable_turns_a_rule_off_and_needs_its_adr(tmp_path):
    entry = f'\n[[tool.agentic-check.disable]]\nrule = "MOD-01"\nadr = "{ADR}"\nreason = "a catalog"\n'
    write_project(tmp_path, BAD, pyproject=PYPROJECT + entry)
    code, report = check_json(tmp_path)
    assert code == 0
    assert "MOD-01" not in [r["id"] for r in report["rules_run"]]
    write_project(tmp_path, BAD, pyproject=PYPROJECT + entry.replace(ADR, "docs/adr/2999-missing.md"))
    code, _, err = check(tmp_path)
    assert code == 2
    assert "ADR file docs/adr/2999-missing.md does not exist" in err


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ("\nlocal = []\n", "unknown key(s) local"),
        ('\n[tool.agentic-check.options.MOD-01]\nsite = ["x"]\n', "unknown key(s) site"),
        ('\n[tool.agentic-check.options.STP-01]\nsites = ["x"]\n', "names unknown rule STP-01"),
        ('\n[tool.agentic-check.options.MOD-01]\nsites = "x"\n', "`sites` must be a list"),
    ],
)
def test_a_configuration_error_exits_2(tmp_path, extra, message):
    write_project(tmp_path, pyproject=PYPROJECT + extra)
    code, _, err = check(tmp_path)
    assert code == 2
    assert message in err


def test_a_misspelt_package_exits_2(tmp_path):
    write_project(tmp_path, pyproject='[tool.agentic-check]\npackage = "acne"\n')
    code, _, err = check(tmp_path)
    assert code == 2
    assert "no module is under the package 'acne'" in err


def test_a_file_that_does_not_parse_is_a_finding(tmp_path):
    write_project(tmp_path, {f"{OM}/broken.py": "def (:\n"})
    code, report = check_json(tmp_path)
    assert code == 1
    assert [(f["rule"], f["path"]) for f in report["findings"]] == [("PARSE", f"{OM}/broken.py")]


def test_a_rule_that_raises_exits_2_and_the_others_still_run(tmp_path, monkeypatch):
    def broken(project):
        raise RuntimeError("boom")

    rule = registry.RULES["TOL-01"]
    monkeypatch.setitem(registry.RULES, "TOL-01", type(rule)(**{**rule.__dict__, "check": broken}))
    write_project(tmp_path, BAD)
    code, out, err = check(tmp_path)
    assert code == 2
    assert "TOL-01 raised RuntimeError: boom" in out
    assert "MOD-01 names the model" in out
    assert "1 rule(s) raised" in err


def test_the_package_is_inferred_without_a_table(tmp_path):
    write_project(tmp_path, pyproject=None)
    write(tmp_path, "pyproject.toml", "[project]\nname = 'acme'\n")
    assert check(tmp_path)[0] == 0


@pytest.mark.skipif(
    (pinned_python(SCAFFOLD) or (0, 0)) > sys.version_info[:2], reason="the scaffold pins a newer Python than this one"
)
def test_the_scaffold_is_clean():
    code, out, err = check(SCAFFOLD)
    assert (code, err) == (0, ""), out
