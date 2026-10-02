#!/usr/bin/env python3
"""Check the frontmatter of every subagent under agents/.

A folder that does not exist holds no agent, and passes. Each
`agents/<name>.md` opens with flat `key: value` frontmatter, read as
strictly as a skill's (`check_skills.py`), and holds:

- `name`, equal to the file's name without `.md`, `agentic-` then
  lowercase words joined by one hyphen each, so it never collides with
  the guideline's agents;
- a non-empty `description`, written as one double-quoted string;
- a count bound: `maxTurns`, a whole number above zero, and the host
  stops the agent after that many turns. The host's own validation
  accepts a missing or misspelled key, or a value that is no number, and
  then only the session's turns or wall time stop an agent that keeps
  reading. The message says which: the key is missing, or its value is
  not a whole number above zero.

The reviewer that `agentic-review-full` fans out to,
`agents/agentic-reviewer.md`, carries the same procedure and the same
report shape as the review skills, and nothing generates it. So, when
the review template `skills/_template/review.SKILL.md` exists, the
reviewer exists too and mirrors it:

- the four decision words (finding, pass, not applicable, unverified)
  appear in bold in the procedure of both, in the same order;
- the report block, from the ```markdown fence to its closing fence,
  is identical in both, the template's `{title}` read as the agent's
  `<group title>`;
- the numbered procedure steps agree in count.

Exit status is non-zero on any failure. Standard library only.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from pathlib import Path

from _common import arguments
from check_skills import FRONTMATTER, NAME, NAME_LIMIT, QUOTED_DESCRIPTION, frontmatter

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "agents"
TEMPLATE = ROOT / "skills" / "_template" / "review.SKILL.md"
REVIEWER = ROOT / "agents" / "agentic-reviewer.md"
DECISIONS = ("finding", "pass", "not applicable", "unverified")
STEP = re.compile(r"^\d+\. ", re.M)
PROCEDURE = re.compile(r"^(?:## Procedure|Procedure\b)[^\n]*\n(.*?)(?=^## Output|^```markdown)", re.M | re.S)
"""The procedure: from its heading to the report block, or to the Output heading before it."""
REPORT = re.compile(r"^```markdown\n.*?^```$", re.M | re.S)
PLACEHOLDERS = {"{title}": "<group title>"}
# A whole number above zero, unquoted.
WHOLE_ABOVE_ZERO = re.compile(r"^[1-9][0-9]*$")
MAX_TURNS = re.compile(r"^maxTurns:[ \t]*(.*?)[ \t]*$", re.M)


def check_agent(path: Path, errors: list[str]) -> None:
    """One agent file: its name, its description, and its turn cap."""
    rel = str(path.relative_to(ROOT))
    text = path.read_text(encoding="utf-8")
    fm = frontmatter(text, errors, rel)
    head = FRONTMATTER.match(text)
    if not fm or head is None:
        errors.append(f"{rel}: missing frontmatter")
        return
    name = fm.get("name", "")
    if name != path.stem:
        errors.append(f"{rel}: name '{name}' differs from its file '{path.stem}'")
    if not NAME.match(name) or len(name) > NAME_LIMIT:
        errors.append(f"{rel}: name '{name}' must match {NAME.pattern}, at most {NAME_LIMIT} characters")
    if not fm.get("description", ""):
        errors.append(f"{rel}: empty description")
    elif not QUOTED_DESCRIPTION.search(head.group(1)):
        errors.append(f"{rel}: description must be one double-quoted string")
    found = MAX_TURNS.search(head.group(1))
    if not found:
        errors.append(f"{rel}: no maxTurns in the frontmatter; bound the agent's turns with `maxTurns: <n>`, n above 0")
    elif not WHOLE_ABOVE_ZERO.match(found.group(1)):
        errors.append(f"{rel}: maxTurns is {found.group(1)!r}, not a whole number above zero")


def procedure(text: str, rel: str, errors: list[str]) -> str:
    """The procedure text: from its heading to the report block."""
    m = PROCEDURE.search(text)
    if not m:
        errors.append(f"{rel}: no procedure before the report block")
        return ""
    return m.group(1)


def report_block(text: str, rel: str, errors: list[str]) -> str:
    """The ```markdown report block, the template's placeholders read as the agent's."""
    m = REPORT.search(text)
    if not m:
        errors.append(f"{rel}: no ```markdown report block")
        return ""
    block = m.group(0)
    for placeholder, stand_in in PLACEHOLDERS.items():
        block = block.replace(placeholder, stand_in)
    return block


def decision_order(proc: str) -> list[str]:
    """The bold decision words a procedure names, in the order it first names them."""
    flat = re.sub(r"\s+", " ", proc)
    found = [(flat.find(f"**{word}**"), word) for word in DECISIONS]
    return [word for pos, word in sorted(found) if pos >= 0]


def check_mirror(errors: list[str]) -> None:
    """When the review template exists, the reviewer exists and mirrors it: decision words, step count, report."""
    if not TEMPLATE.is_file():
        return
    t_rel, a_rel = str(TEMPLATE.relative_to(ROOT)), str(REVIEWER.relative_to(ROOT))
    if not REVIEWER.is_file():
        errors.append(f"{a_rel}: missing; agentic-review-full fans out to it, and it mirrors {t_rel}")
        return
    t_text, a_text = TEMPLATE.read_text(encoding="utf-8"), REVIEWER.read_text(encoding="utf-8")
    t_proc, a_proc = procedure(t_text, t_rel, errors), procedure(a_text, a_rel, errors)
    for rel, proc in ((t_rel, t_proc), (a_rel, a_proc)):
        missing = [w for w in DECISIONS if w not in decision_order(proc)]
        if proc and missing:
            errors.append(f"{rel}: procedure lacks the bold decision word(s) {', '.join(missing)}")
    if decision_order(t_proc) != decision_order(a_proc):
        errors.append(f"{a_rel}: decision words are in a different order than {t_rel}")
    t_steps, a_steps = len(STEP.findall(t_proc)), len(STEP.findall(a_proc))
    if t_steps != a_steps:
        errors.append(f"{a_rel}: {a_steps} procedure steps, {t_rel} has {t_steps}")
    t_block, a_block = report_block(t_text, t_rel, errors), report_block(a_text, a_rel, errors)
    if t_block and a_block and t_block != a_block:
        errors.append(f"{a_rel}: report block differs from {t_rel}")


def main(argv: Sequence[str] = ()) -> int:
    arguments(__doc__, argv)
    errors: list[str] = []
    agents = sorted(AGENTS.glob("*.md")) if AGENTS.is_dir() else []
    for path in agents:
        check_agent(path, errors)
    check_mirror(errors)
    if errors:
        print("\n".join(errors))
        print(f"\n{len(errors)} problem(s) in {len(agents)} agent(s)")
        return 1
    mirrored = f"; {REVIEWER.relative_to(ROOT)} mirrors {TEMPLATE.relative_to(ROOT)}" if TEMPLATE.is_file() else ""
    print(f"agents ok: {len(agents)} agent(s){mirrored}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
