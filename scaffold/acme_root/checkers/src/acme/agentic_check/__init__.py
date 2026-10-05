"""agentic-check: the static checker of the agentic_core spec's lenses.

It decides the parts of the engine's lenses a parser can decide without
guessing, by reading a project's source with `ast`. It never imports
the code it checks. Standard library only.

`__version__` is the engine release this checker ships with, which the
engine's `scripts/check_version.py` holds equal to its plugin manifest.
"""

__version__ = "0.5.1"
