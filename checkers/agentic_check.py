#!/usr/bin/env python3
"""Run agentic-check from a checkout, without installing it.

`python3 checkers/agentic_check.py [ARGS]` puts `checkers/src` first on
`sys.path` and calls the same `main` as the `agentic-check` console
script, so a project or CI can run the checker of a checkout.
"""

import sys
from pathlib import Path

if sys.version_info < (3, 11):
    print("agentic-check: error: needs Python 3.11 or later", file=sys.stderr)
    sys.exit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from agentic_check.cli import main

if __name__ == "__main__":
    sys.exit(main())
