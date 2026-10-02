"""A tiny project in the engine's shape for the agentic-check tests, and a way to run the CLI on one.

`write_project` builds a product named `acme` with the parts the rules
read: the tool contract and one tool, the steps table and its purge,
the fills and the price table, a manager over an injected gate, a
memory storage impl, and a root. Every rule passes on it. Each test
adds, overrides, or drops the files its rule reads. `check` runs
`agentic_check.cli.main` on the tree and returns the exit status and
what it printed.
"""

from __future__ import annotations

import contextlib
import io
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agentic_check.cli import main

PYPROJECT = '[tool.agentic-check]\npackage = "acme"\n'
ADR = "docs/adr/2001-a-catalog-of-models.md"
OM = "om/src/acme/om"

TOOL = f"{OM}/tools/echo.py"
POSTGRES = f"{OM}/steps/storage/impl/postgres.py"
MANAGER = f"{OM}/budgets/impl/manager.py"

BASE: dict[str, str] = {
    "om/src/acme/__init__.py": "",
    f"{OM}/__init__.py": "",
    f"{OM}/base.py": (
        "from pydantic import BaseModel, ConfigDict\n\n\n"
        'class Platform(BaseModel):\n    model_config = ConfigDict(frozen=True, extra="forbid")\n'
    ),
    # the tool contract, re-exported by its package, and one tool built on it
    f"{OM}/tools/__init__.py": "from .tool import ToolInterface as ToolInterface\n",
    f"{OM}/tools/types/__init__.py": "",
    f"{OM}/tools/types/tool.py": (
        "from datetime import timedelta\nfrom enum import StrEnum\n\nfrom acme.om.base import Platform\n\n\n"
        'class Effect(StrEnum):\n    READ_ONLY = "read_only"\n\n\n'
        'class ToolMode(StrEnum):\n    SYNC = "sync"\n\n\n'
        "class ToolInput(Platform):\n    pass\n\n\n"
        "class ToolSpec(Platform):\n    name: str\n    input_model: type[ToolInput]\n    output_model: type[Platform]\n"
        "    timeout: timedelta\n    authorization_class: str\n    effect: Effect\n    interruptible: bool\n"
        "    mode: ToolMode = ToolMode.SYNC\n"
    ),
    f"{OM}/tools/tool.py": (
        "from abc import ABC, abstractmethod\n\nfrom acme.om.tools.types.tool import ToolSpec\n\n\n"
        "class ToolInterface(ABC):\n    @property\n    @abstractmethod\n    def spec(self) -> ToolSpec: ...\n\n\n"
        "class JobToolInterface(ToolInterface):\n    pass\n"
    ),
    TOOL: (
        "from datetime import timedelta\n\nfrom acme.om.base import Platform\nfrom acme.om.tools import ToolInterface\n"
        "from acme.om.tools.types.tool import Effect, ToolInput, ToolMode, ToolSpec\n\n\n"
        "class EchoInput(ToolInput):\n    text: str\n\n\n"
        "class EchoOutput(Platform):\n    text: str\n\n\n"
        "class EchoTool(ToolInterface):\n"
        "    @property\n    def spec(self) -> ToolSpec:\n        return ToolSpec(\n"
        '            name="echo",\n            input_model=EchoInput,\n            output_model=EchoOutput,\n'
        '            timeout=timedelta(seconds=5),\n            authorization_class="read",\n'
        "            effect=Effect.READ_ONLY,\n            interruptible=False,\n            mode=ToolMode.SYNC,\n        )\n"
    ),
    # the history: its table, a step type, and a storage that appends and purges
    f"{OM}/steps/__init__.py": "",
    f"{OM}/steps/types/__init__.py": "",
    f"{OM}/steps/types/step.py": "from acme.om.base import Platform\n\n\nclass Step(Platform):\n    seq: int\n    text: str\n",
    f"{OM}/steps/storage/__init__.py": "",
    f"{OM}/steps/storage/tables/__init__.py": "",
    f"{OM}/steps/storage/tables/steps.py": (
        'class Steps:\n    __tablename__ = "steps"\n\n\nclass StepCursors:\n    __tablename__ = "step_cursors"\n'
    ),
    f"{OM}/steps/storage/impl/__init__.py": "",
    POSTGRES: (
        "from sqlalchemy import delete, insert, update\n\n"
        "from acme.om.steps.storage.tables.steps import StepCursors, Steps\n\n\n"
        "class StepStoragePostgresImpl:\n"
        "    async def append(self, rows: list[dict]) -> None:\n"
        '        """No UPDATE steps here: a step is written once."""\n'
        "        stored: dict = {}\n        stored.update({})\n"
        "        insert(Steps).values(rows)\n        update(StepCursors).values(head=1)\n\n"
        "    async def purge_history(self, session_id: str) -> None:\n"
        "        delete(Steps).where(Steps.session_id == session_id)\n"
        '        "DELETE FROM activity.steps WHERE session_id = :id"\n'
    ),
    # the fills and the price table: the two places a model is named
    f"{OM}/models/__init__.py": "",
    f"{OM}/models/impl/__init__.py": "",
    f"{OM}/models/impl/resolver.py": 'FILLS = [dict(provider="anthropic", model="claude-x")]\n',
    f"{OM}/budgets/__init__.py": "",
    f"{OM}/budgets/impl/__init__.py": "",
    f"{OM}/budgets/impl/pricing.py": 'def row(model: str) -> dict:\n    return {"model": model}\n\n\nROWS = [row("claude-x")]\n',
    # a manager over an injected gate, a memory storage, and the root that wires a null gate
    f"{OM}/budgets/gate.py": "from abc import ABC\n\n\nclass BudgetGateInterface(ABC):\n    pass\n",
    MANAGER: (
        "from acme.om.budgets.gate import BudgetGateInterface\n\n\n"
        "class BudgetsManagerImpl:\n    def __init__(self, gate: BudgetGateInterface) -> None:\n        self._gate = gate\n\n"
        "    async def charge(self, fill) -> None:\n        await self.call(model=fill.model)\n"
    ),
    f"{OM}/budgets/storage/__init__.py": "",
    f"{OM}/budgets/storage/impl/__init__.py": "",
    f"{OM}/budgets/storage/impl/memory.py": (
        "class OutboxLandingInterface:\n    pass\n\n\n"
        "class BudgetStorageMemoryImpl:\n    def __init__(self, outbox: OutboxLandingInterface | None = None) -> None:\n"
        "        self._outbox = outbox\n"
    ),
    f"{OM}/root.py": (
        "from acme.om.budgets.gate import BudgetGateInterface\n\n\n"
        "def build(gate: BudgetGateInterface | None = None) -> None:\n    pass\n"
    ),
    # a module outside the tools that may start a process
    "infra/src/acme/infra/__init__.py": "",
    "infra/src/acme/infra/docker.py": "import subprocess\n\n\ndef run() -> None:\n    subprocess.run(['true'])\n",
    ADR: "# 2001. A catalog of models\n\nDate: 2026-10-02\n\n## Context\n\nA catalog.\n\n## Decision\n\nIt names models.\n",
}


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_project(root: Path, files: Mapping[str, str | None] | None = None, *, pyproject: str | None = PYPROJECT) -> Path:
    """Write the base tree under `root`, then `files` over it; a `None` text drops a base file."""
    if pyproject is not None:
        write(root, "pyproject.toml", pyproject)
    for rel, text in {**BASE, **(files or {})}.items():
        if text is not None:
            write(root, rel, text)
    return root


def check(root: Path, *args: str) -> tuple[int, str, str]:
    """Run the CLI on `root`; (exit status, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(["--root", str(root), *args])
    return code, out.getvalue(), err.getvalue()


def check_json(root: Path, *args: str) -> tuple[int, dict[str, Any]]:
    """Run the CLI with `--format json`; (exit status, the parsed report)."""
    code, out, err = check(root, "--format", "json", *args)
    assert out, err
    return code, json.loads(out)


def found(root: Path, rule: str) -> list[tuple[str, int, str]]:
    """(path, line, message) of every finding of one rule, running that rule alone."""
    _, report = check_json(root, "--rule", rule)
    return [(f["path"], f["line"], f["message"]) for f in report["findings"] if f["rule"] == rule]
