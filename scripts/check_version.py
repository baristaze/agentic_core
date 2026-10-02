#!/usr/bin/env python3
"""Check that every copy of the release version agrees with the plugin manifest.

`.claude-plugin/plugin.json` is the one source. The copies it is checked
against:
- `.claude-plugin/marketplace.json`: the `version` of every plugin entry;
- `CHANGELOG.md`: the first `## MAJOR.MINOR.PATCH` heading, the latest
  release (an `## Unreleased` heading above it is fine). Before the
  first release the changelog has no release heading, and the version
  is `0.0.0`;
- `scaffold/acme_root/checkers/src/acme/agentic_check/__init__.py`: the
  `__version__` of the checker the scaffold carries.

A copy whose file is missing is not checked.

Exit status is non-zero when any copy disagrees. Standard library only.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Sequence

from _common import ROOT, arguments

PLUGIN = ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
CHANGELOG = ROOT / "CHANGELOG.md"
CHECKER_INIT = ROOT / "scaffold" / "acme_root" / "checkers" / "src" / "acme" / "agentic_check" / "__init__.py"

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
RELEASE_HEADING = re.compile(r"^## (\d+\.\d+\.\d+)\b")
UNRELEASED = "0.0.0"
DUNDER_VERSION = re.compile(r'^__version__\s*=\s*"([^"]*)"', re.MULTILINE)


def source() -> str:
    return str(json.loads(PLUGIN.read_text(encoding="utf-8")).get("version", ""))


def check(version: str, errors: list[str]) -> None:
    for i, plugin in enumerate(json.loads(MARKETPLACE.read_text(encoding="utf-8")).get("plugins", [])):
        found = str(plugin.get("version", ""))
        if found != version:
            errors.append(f"{MARKETPLACE.relative_to(ROOT)}: plugins[{i}].version is {found!r}, plugin.json says {version!r}")
    for ln, line in enumerate(CHANGELOG.read_text(encoding="utf-8").splitlines(), start=1):
        m = RELEASE_HEADING.match(line)
        if m:
            if m.group(1) != version:
                errors.append(f"{CHANGELOG.relative_to(ROOT)}:{ln}: latest release is {m.group(1)}, plugin.json says {version}")
            break
    else:
        if version != UNRELEASED:
            errors.append(f"{CHANGELOG.relative_to(ROOT)}: no release heading, and plugin.json says {version}")
    if CHECKER_INIT.is_file():
        m = DUNDER_VERSION.search(CHECKER_INIT.read_text(encoding="utf-8"))
        declared = m.group(1) if m else None
        if declared != version:
            errors.append(f"{CHECKER_INIT.relative_to(ROOT)}: __version__ is {declared!r}, plugin.json says {version!r}")


def main(argv: Sequence[str] = ()) -> int:
    arguments(__doc__, argv)
    errors: list[str] = []
    version = source()
    if not SEMVER.match(version):
        errors.append(f"{PLUGIN.relative_to(ROOT)}: version {version!r} is not MAJOR.MINOR.PATCH")
    else:
        check(version, errors)
    if errors:
        print("\n".join(errors))
        print(f"\n{len(errors)} version mismatch(es)")
        return 1
    print(f"version ok: {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
