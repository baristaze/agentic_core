"""scripts/check_lenses.py: the lens format, its citations of the spec, and the names a lens quotes."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

SPEC = """\
# agentic_core

## Contents

- [Steps](#steps)

## Steps

`core`

A response points at its request with `responds_to`.

### One Event, One Step

**Pairs.** A request and its response are two steps.

## Loops, Runs, and Sessions

### Durable by Default

`core`

A run takes a writer epoch before it reads the history.

<!-- agents-only
The epoch is installed by `compare_and_set` on the cursor row.
-->

<!-- a note for the editor: `hidden_name` -->

## Bounds and Budgets

### One Gate, Before the Call

Every model call passes one gate.

### Conventions

`style`

A gate is named for what it guards.
"""

README = """\
# Lenses

| Group id | Prefix | File       | Covers |
|----------|--------|------------|--------|
| `steps`  | `STP`  | `steps.md` | Steps |
"""

LENSES = """\
# Steps

## STP-01 A response points at its request

**Principle.** A response points at its request with `responds_to`.

**Source.** Steps, One Event, One Step.

**Look for.** Every response step.

**Violation.** A response with no pointer.

**Severity.** medium

**Check.** review

## STP-02 A stale writer is refused

**Principle.** A run takes a writer epoch before it reads the history.

**Source.** Loops, Runs, and Sessions, Durable by Default; Bounds and
Budgets, One Gate, Before the Call.

**Look for.** Where a run takes its epoch.

**Violation.** An append that ignores the epoch.

**Severity.** high

**Check.** review
"""


class Tree:
    """A small repository under a temporary directory, which a test bends and then checks."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def read(self, rel: str) -> str:
        return (self.root / rel).read_text(encoding="utf-8")

    def edit(self, rel: str, old: str, new: str) -> None:
        text = self.read(rel)
        assert old in text, f"{old!r} is not in {rel}"
        self.write(rel, text.replace(old, new, 1))


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Tree:
    t = Tree(tmp_path)
    t.write("agentic_core_spec.md", SPEC)
    t.write("lenses/README.md", README)
    t.write("lenses/steps.md", LENSES)
    return t


@pytest.fixture
def lenses(tree: Tree, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = importlib.import_module("check_lenses")
    monkeypatch.setattr(module, "ROOT", tree.root)
    monkeypatch.setattr(module, "SPEC", tree.root / "agentic_core_spec.md")
    monkeypatch.setattr(module, "LENSES", tree.root / "lenses")
    monkeypatch.setattr(module, "README", tree.root / "README.md")
    monkeypatch.setattr(module, "RULES", tree.root / "checkers" / "src" / "agentic_check" / "rules")
    return module


def run(lenses: ModuleType, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = lenses.main()
    return code, capsys.readouterr().out


def test_valid_tree_passes(lenses, capsys):
    code, out = run(lenses, capsys)
    assert code == 0, out
    assert "lenses ok: 2 lenses in 1 groups" in out


def test_a_subsection_with_a_comma_belongs_to_its_own_section(tree, lenses, capsys):
    tree.edit("lenses/steps.md", "Bounds and\nBudgets, One Gate, Before the Call.", "One Gate, Before the Call.")
    code, out = run(lenses, capsys)
    assert code == 1
    assert "'One Gate, Before the Call' is neither a section nor a subsection of 'Loops, Runs, and Sessions'" in out


def test_unknown_section_fails(tree, lenses, capsys):
    tree.edit("lenses/steps.md", "**Source.** Steps, One Event", "**Source.** Step, One Event")
    code, out = run(lenses, capsys)
    assert code == 1
    assert "'Step, One Event, One Step' is not a section of agentic_core_spec.md" in out


def test_unknown_subsection_of_a_title_with_commas_fails(tree, lenses, capsys):
    tree.edit("lenses/steps.md", "Sessions, Durable by Default", "Sessions, Recovery")
    code, out = run(lenses, capsys)
    assert code == 1
    assert "'Loops, Runs, and Sessions' has no subsection 'Recovery'" in out


def test_a_bare_subsection_belongs_to_the_section_before_it(tree, lenses, capsys):
    tree.edit("lenses/steps.md", "Bounds and\nBudgets, One Gate, Before the Call.", "Durable by Default.")
    assert lenses.main() == 0
    tree.edit("lenses/steps.md", "Default; Durable by Default.", "Default; One Event, One Step.")
    code, out = run(lenses, capsys)
    assert code == 1
    assert "'One Event, One Step' is neither a section nor a subsection of 'Loops, Runs, and Sessions'" in out


def test_section_cited_by_number_fails(tree, lenses, capsys):
    tree.edit("lenses/steps.md", "**Source.** Steps, One Event", "**Source.** Section 2, One Event")
    code, out = run(lenses, capsys)
    assert code == 1
    assert "cites a section by number" in out


def test_labels_must_be_paragraphs_of_the_subsection(tree, lenses, capsys):
    tree.edit("lenses/steps.md", "Steps, One Event, One Step.", "Steps, One Event, One Step (Pairs).")
    assert lenses.main() == 0
    tree.edit("lenses/steps.md", "(Pairs)", "(Turns)")
    code, out = run(lenses, capsys)
    assert code == 1
    assert "'Steps, One Event, One Step' has no paragraph labelled '**Turns.**'" in out


def test_bad_severity_fails(tree, lenses, capsys):
    tree.edit("lenses/steps.md", "**Severity.** medium", "**Severity.** critical")
    code, out = run(lenses, capsys)
    assert code == 1
    assert "severity 'critical' is not high, medium, or low" in out


def test_ids_out_of_order_fail(tree, lenses, capsys):
    tree.edit("lenses/steps.md", "## STP-02 ", "## STP-03 ")
    code, out = run(lenses, capsys)
    assert code == 1
    assert "expected id STP-02, found STP-03" in out


def test_a_prefix_other_than_the_listed_one_fails(tree, lenses, capsys):
    tree.edit("lenses/steps.md", "## STP-01 ", "## STE-01 ")
    code, out = run(lenses, capsys)
    assert code == 1
    assert "prefix STE is not STP" in out


def test_two_groups_sharing_a_prefix_fail(tree, lenses, capsys):
    tree.write("lenses/loops.md", LENSES)
    tree.edit("lenses/README.md", "| Steps |\n", "| Steps |\n| `loops` | `STP` | `loops.md` | Loops |\n")
    code, out = run(lenses, capsys)
    assert code == 1
    assert "lists prefix STP for both steps and loops" in out


def test_a_missing_check_line_fails(tree, lenses, capsys):
    tree.edit("lenses/steps.md", "**Severity.** medium\n\n**Check.** review\n", "**Severity.** medium\n")
    code, out = run(lenses, capsys)
    assert code == 1
    assert "fields are" in out


def test_a_check_line_is_review_or_one_of_the_checkers_two_sentences(tree, lenses, capsys):
    tree.edit("lenses/steps.md", "**Check.** review", "**Check.** a script decides it.")
    code, out = run(lenses, capsys)
    assert code == 1
    assert "Check reads 'a script decides it.'" in out


RULE = """from agentic_check.registry import rule


@rule("STP-01", coverage="{coverage}", summary="One sentence.")
def a_rule(project):
    return []
"""


def test_a_check_line_that_names_the_checker_agrees_with_its_rule(tree, lenses, capsys):
    tree.edit("lenses/steps.md", "**Check.** review", "**Check.** `agentic-check` decides it.")
    code, out = run(lenses, capsys)
    assert code == 1
    assert "STP-01 says agentic-check decides it, and no rule of that id is registered" in out
    tree.write("checkers/src/agentic_check/rules/steps.py", RULE.format(coverage="partial"))
    code, out = run(lenses, capsys)
    assert code == 1
    assert "STP-01's Check line says full, and its rule registers partial" in out
    tree.write("checkers/src/agentic_check/rules/steps.py", RULE.format(coverage="full"))
    assert lenses.main() == 0
    capsys.readouterr()
    partial = "**Check.** `agentic-check` decides that a response\npoints at its request; the rest is judged."
    tree.edit("lenses/steps.md", "**Check.** `agentic-check` decides it.", partial)
    tree.write("checkers/src/agentic_check/rules/steps.py", RULE.format(coverage="partial"))
    assert lenses.main() == 0


def test_a_registered_rule_needs_its_lens_to_name_the_checker(tree, lenses, capsys):
    tree.write("checkers/src/agentic_check/rules/steps.py", RULE.format(coverage="full"))
    code, out = run(lenses, capsys)
    assert code == 1
    assert "rule STP-01 is registered, and lens STP-01 has no Check line that names agentic-check" in out


def test_fields_out_of_order_fail(tree, lenses, capsys):
    tree.edit(
        "lenses/steps.md",
        "**Look for.** Every response step.\n\n**Violation.** A response with no pointer.",
        "**Violation.** A response with no pointer.\n\n**Look for.** Every response step.",
    )
    code, out = run(lenses, capsys)
    assert code == 1
    assert "fields are" in out


def test_unlisted_and_missing_files_fail(tree, lenses, capsys):
    tree.write("lenses/extra.md", "# Extra\n\n## EXT-01 One\n")
    tree.edit("lenses/README.md", "| Steps |\n", "| Steps |\n| `trust` | `TRU` | `trust.md` | Trust |\n")
    code, out = run(lenses, capsys)
    assert code == 1
    assert "lenses/extra.md is not listed" in out
    assert "lists trust -> trust.md, file missing" in out


def test_principle_over_a_hundred_and_twenty_words_fails(tree, lenses, capsys):
    tree.edit("lenses/steps.md", "**Principle.** A response points", "**Principle.** " + "word " * 115 + "A response points")
    code, out = run(lenses, capsys)
    assert code == 1
    assert "Principle is 123 words, limit 120" in out


def test_four_sentences_in_violation_fail(tree, lenses, capsys):
    tree.edit("lenses/steps.md", "**Violation.** A response with no pointer.", "**Violation.** One. Two. Three. Four.")
    code, out = run(lenses, capsys)
    assert code == 1
    assert "Violation is 4 sentences, limit 3" in out


def test_line_wider_than_eighty_columns_fails(tree, lenses, capsys):
    tree.edit("lenses/steps.md", "**Look for.** Every response step.", "**Look for.** " + "x" * 70)
    code, out = run(lenses, capsys)
    assert code == 1
    assert "84 columns, limit 80" in out


def test_an_identifier_outside_the_cited_section_fails(tree, lenses, capsys):
    tree.edit("lenses/steps.md", "with `responds_to`.", "with `responds_to` and `compare_and_set`.")
    code, out = run(lenses, capsys)
    assert code == 1
    assert "STP-01 quotes `compare_and_set`, which Steps does not hold" in out


def test_an_agents_only_block_holds_an_identifier_and_a_plain_comment_does_not(tree, lenses, capsys):
    tree.edit("lenses/steps.md", "before it reads the history.", "before it reads the history,\nby `compare_and_set`.")
    assert lenses.main() == 0
    tree.edit("lenses/steps.md", "by `compare_and_set`.", "by `hidden_name`.")
    code, out = run(lenses, capsys)
    assert code == 1
    assert "STP-02 quotes `hidden_name`" in out


def test_an_identifier_the_spec_never_names_is_an_example_in_a_violation(tree, lenses, capsys):
    tree.edit("lenses/steps.md", "A response with no pointer.", "A response built by `make_response()` with no pointer.")
    assert lenses.main() == 0


def test_a_lone_backticked_word_under_a_heading_must_be_a_tag(tree, lenses, capsys):
    tree.edit("agentic_core_spec.md", "## Steps\n\n`core`", "## Steps\n\n`invariant`")
    code, out = run(lenses, capsys)
    assert code == 1
    assert "`invariant` is no tag" in out


def test_a_lens_citing_only_style_sections_is_low(tree, lenses, capsys):
    tree.edit("lenses/steps.md", "**Source.** Steps, One Event, One Step.", "**Source.** Bounds and Budgets, Conventions.")
    tree.edit("lenses/steps.md", "with `responds_to`.", "with a pointer.")
    code, out = run(lenses, capsys)
    assert code == 1
    assert "STP-01 is medium, and every section it cites is tagged `style`" in out


def test_a_shape_names_a_path_of_the_scaffold_that_exists(tree, lenses, capsys):
    shape = "**Severity.** medium\n\n**Shape.** `scaffold/acme_root/engine/loop.py`\n\n**Check.** review"
    tree.edit("lenses/steps.md", "**Severity.** medium\n\n**Check.** review", shape)
    code, out = run(lenses, capsys)
    assert code == 1
    assert "Shape names `scaffold/acme_root/engine/loop.py`, which does not exist" in out
    tree.write("scaffold/acme_root/engine/loop.py", "")
    assert lenses.main() == 0


def test_a_stated_count_must_equal_the_catalog(tree, lenses, capsys):
    tree.write("README.md", "# agentic_core\n\nThe spec has 3 lenses.\n")
    code, out = run(lenses, capsys)
    assert code == 1
    assert "README.md:3: says 3 lenses, the catalog has 2" in out


def test_lens_syntax_inside_fenced_code_is_an_example(tree, lenses, capsys):
    example = "```markdown\n## STP-09 An example lens\n\n**Principle.** Shown, not counted.\n```\n"
    tree.edit(
        "lenses/steps.md",
        "**Violation.** A response with no pointer.\n",
        f"**Violation.** A response with no pointer, like this:\n\n{example}",
    )
    code, out = run(lenses, capsys)
    assert code == 0, out
    assert "lenses ok: 2 lenses" in out
