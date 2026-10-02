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


def main(argv: Sequence[str] = ()) -> int:
    arguments(__doc__, argv)
    errors: list[str] = []
    agents = sorted(AGENTS.glob("*.md")) if AGENTS.is_dir() else []
    for path in agents:
        check_agent(path, errors)
    if errors:
        print("\n".join(errors))
        print(f"\n{len(errors)} problem(s) in {len(agents)} agent(s)")
        return 1
    print(f"agents ok: {len(agents)} agent(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
