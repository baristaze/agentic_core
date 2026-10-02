"""One small repository tree, valid under every checker, that each test bends.

The scripts locate their inputs through module-level paths derived from
`ROOT`; `repo` builds the tree under a temporary directory and points
each imported script at it, so a test edits a file and calls `main()`.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


SPEC = """\
# agentic_core

*Specification of an agentic engine.*

## How to Read This

Read [the steps](#steps) first, then [their record](#the-record-1).

## Contents

- [Steps](#steps)
- [Loops and Sessions](#loops-and-sessions)

## Steps

### The Record

A step is written once.

``` python
# not a heading: fenced code
```

## Loops and Sessions

### The Record

A loop references steps, never copies them.
"""

LENS_STEPS = """\
# Steps

## STP-01 A step is written once

**Source.** Steps, The Record.
"""

LENSES_README = """\
# Lenses

## Groups

| Group id | Prefix | File       | Covers |
|----------|--------|------------|--------|
| `steps`  | `STP`  | `steps.md` | Steps  |
"""

TEMPLATE = """\
---
name: agentic-review-{group}
description: "Review code through the {title} lenses: {covers}. Use as one leg of agentic-review-full."
allowed-tools: Read, Grep, Bash(git diff:*)
---

# agentic-review-{group}

Read `../../lenses/{group}.md` and the spec at `../../agentic_core_spec.md`,
from this skill's folder as `realpath` resolves it. Take the scope from
`git diff`. Never edit, stage, or commit.

## Procedure

1. Read the lens file end to end.
2. For every lens decide **finding**, **pass**, **not applicable**, or
   **unverified**.
3. Write the report in the format below.

## Output

```markdown
# Engine review: {title}

**Scope.** <what was reviewed>

## Findings

- **<LENS-ID> <severity>** `<path>:<line>` <what breaks the rule>.
```
"""


def render_template(group: str, title: str, covers: str) -> str:
    """The review template with one group filled in, as `make gen-skills` writes it."""
    return TEMPLATE.replace("{group}", group).replace("{title}", title).replace("{covers}", covers)


REVIEW = render_template("steps", "Steps", "Steps")

REVIEW_FULL = """\
---
name: agentic-review-full
description: "Full review: runs agentic-review-steps and merges the report."
allowed-tools: Read, Agent
---

# agentic-review-full

Run `agentic-review-steps` on the scope and merge the reports.
"""

SCAFFOLD = """\
---
name: agentic-scaffold-tool
description: "Create a tool the way the spec prescribes."
allowed-tools: Read, Write, Bash(make check)
---

# agentic-scaffold-tool

## Procedure

1. Write the tool.
2. Run `make check`.
"""

AGENT = """\
---
name: agentic-reviewer
description: "Reviews a scope through one lens group of the agentic_core spec."
tools: Read, Grep, Bash(git diff:*)
maxTurns: 80
---

You are a reviewer.

Procedure (the same as the `agentic-review-<group>` skills):

1. Read the lens file end to end.
2. For every lens decide **finding**, **pass**, **not
   applicable**, or **unverified**.
3. Write the report in the format below.

```markdown
# Engine review: <group title>

**Scope.** <what was reviewed>

## Findings

- **<LENS-ID> <severity>** `<path>:<line>` <what breaks the rule>.
```
"""

PLUGIN = '{"name": "agentic-core", "version": "1.2.3"}\n'
MARKETPLACE = '{"name": "agentic-core", "plugins": [{"name": "agentic-core", "version": "1.2.3"}]}\n'
CHANGELOG = "# Changelog\n\n## Unreleased\n\n## 1.2.3 (2026-01-01)\n\n- First.\n\n## 1.2.2 (2025-12-01)\n\n- Older.\n"
README = "# agentic_core\n\nSee [the spec](agentic_core_spec.md#loops-and-sessions).\n"


class Repo:
    """A fixture tree plus the scripts pointed at it."""

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root = root
        self.monkeypatch = monkeypatch

    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def read(self, rel: str) -> str:
        return (self.root / rel).read_text(encoding="utf-8")

    def edit(self, rel: str, old: str, new: str) -> None:
        text = self.read(rel)
        assert old in text, f"{rel} does not contain {old!r}"
        self.write(rel, text.replace(old, new))

    def script(self, name: str):
        """Import a script and repoint every path it derives from ROOT."""
        mod = importlib.import_module(name)
        for attr, value in vars(mod).items():
            if isinstance(value, Path) and attr.isupper():
                rel = value.relative_to(SCRIPTS.parent)
                self.monkeypatch.setattr(mod, attr, self.root / rel)
        return mod


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Repo:
    r = Repo(tmp_path, monkeypatch)
    r.write("agentic_core_spec.md", SPEC)
    r.write("README.md", README)
    r.write("CHANGELOG.md", CHANGELOG)
    r.write("lenses/README.md", LENSES_README)
    r.write("lenses/steps.md", LENS_STEPS)
    r.write("skills/_template/review.SKILL.md", TEMPLATE)
    r.write("skills/agentic-review-steps/SKILL.md", REVIEW)
    r.write("skills/agentic-review-full/SKILL.md", REVIEW_FULL)
    r.write("skills/agentic-scaffold-tool/SKILL.md", SCAFFOLD)
    r.write("agents/agentic-reviewer.md", AGENT)
    r.write(".claude-plugin/plugin.json", PLUGIN)
    r.write(".claude-plugin/marketplace.json", MARKETPLACE)
    return r
