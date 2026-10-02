#!/usr/bin/env python3
"""Generate the Contents of agentic_core_spec.md from its headings.

The Contents is the list under the `## Contents` heading, up to the next
heading: one entry per section (`##`) that follows it, in order, each a
link to the heading's anchor. The sections above it are the preface a
reader meets first, so the list leaves them out. Headings inside fenced
code or an HTML comment, an agents-only block among them, are ignored: a
rendered page shows neither. Anchors come from `scripts/_common.py`, the
same rule `check_links.py` resolves them with, so every link the list
emits is one that checker accepts, a repeated heading included.

`--check` exits non-zero when the list on disk differs from what the
headings produce, and never writes. Any other argument is refused with
exit status 2, so a mistyped flag cannot fall through to a write.
Standard library only.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from _common import ROOT, anchors, arguments, heading_lines, plain

SPEC = ROOT / "agentic_core_spec.md"
CONTENTS = "Contents"
CHECK_HELP = "exit 1 when the list on disk is stale; never write"


def render(text: str) -> str:
    """The Contents list the headings produce: every `##` section after `## Contents`, linked to its anchor."""
    lines: list[str] = []
    after = False
    for level, title, anchor in anchors(text):
        if level != 2:
            continue
        if after:
            lines.append(f"- [{plain(title)}](#{anchor})")
        elif title == CONTENTS:
            after = True
    return "\n".join(lines)


def regenerated(text: str) -> str | None:
    """The text with its Contents list rewritten from the headings, or None when it has no `## Contents`."""
    found = heading_lines(text)
    at = next((i for i, (_, level, title) in enumerate(found) if level == 2 and title == CONTENTS), None)
    if at is None:
        return None
    lines = text.split("\n")
    start = found[at][0] + 1
    end = found[at + 1][0] if at + 1 < len(found) else len(lines)
    listed = render(text)
    return "\n".join([*lines[:start], "", *([listed] if listed else []), "", *lines[end:]])


def main(argv: Sequence[str] = ()) -> int:
    args = arguments(__doc__, argv, check=CHECK_HELP)
    name = SPEC.name
    text = SPEC.read_text(encoding="utf-8")
    new = regenerated(text)
    if new is None:
        print(f"{name}: no ## {CONTENTS} heading")
        return 1
    if new == text:
        print("toc ok")
        return 0
    if args.check:
        print(f"{name}: the Contents is stale (run `make gen-toc`)")
        return 1
    SPEC.write_text(new, encoding="utf-8")
    print("toc: regenerated")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
