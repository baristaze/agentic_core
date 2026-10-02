"""checkers/src/agentic_check/rules: each rule fires on a bad tree and stays quiet on the good one.

The base tree of `agentic_check_fixtures` is clean under every rule, so
each test plants one breach and reads the rule that decides it.
"""

import pytest

pytest.importorskip("tomllib")

from agentic_check_fixtures import BASE, MANAGER, OM, POSTGRES, TOOL, check, found, write_project


def test_the_base_tree_is_clean_under_every_rule(tmp_path):
    write_project(tmp_path)
    code, out, err = check(tmp_path)
    assert (code, err) == (0, ""), out
    assert out.startswith("agentic-check ok: 6 rule(s)")


# --- STP-07: a step is written once, and only the purge removes it

STORAGE = (
    "from typing import cast\n\nimport sqlalchemy as sa\nfrom sqlalchemy import Table, delete, update\n"
    "from sqlalchemy.dialects.postgresql import insert as pg_insert\n\n"
    "from acme.om.steps.storage.tables.steps import Steps\n\n\n"
    "def fix(step_id: str) -> None:\n"
    "    update(Steps).where(Steps.id == step_id)\n"
    "    table = cast(Table, Steps.__table__)\n"
    "    sa.update(table).values(text='')\n"
    "    Steps.__table__.update()\n"
    "    delete(Steps).where(Steps.id == step_id)\n"
    "    pg_insert(Steps).values(id=step_id).on_conflict_do_update(index_elements=['id'], set_={'text': ''})\n"
    "    \"UPDATE activity.steps SET text = ''\"\n"
    "    'DELETE FROM \"steps\" WHERE id = :id'\n"
    "    \"INSERT INTO steps (id) VALUES (:id) ON CONFLICT (id) DO UPDATE SET text = ''\"\n"
)


def test_stp07_an_update_of_a_step_and_a_delete_outside_a_purge_are_findings(tmp_path):
    write_project(tmp_path, {f"{OM}/steps/storage/impl/repair.py": STORAGE})
    hits = found(tmp_path, "STP-07")
    assert [line for _, line, _ in hits] == [11, 13, 14, 15, 16, 17, 18, 19]
    assert all(path == f"{OM}/steps/storage/impl/repair.py" for path, _, _ in hits)
    messages = [m.split(";")[0] for _, _, m in hits]
    assert messages == [
        "updates a step",
        "updates a step",
        "updates a step",
        "deletes steps outside a purge",
        "updates a step",
        "holds SQL that updates a step",
        "holds SQL that deletes steps outside a purge",
        "holds SQL that updates a step",
    ]


def test_stp07_the_purge_deletes_and_the_cursor_is_updated(tmp_path):
    write_project(tmp_path)
    assert found(tmp_path, "STP-07") == []
    text = BASE[POSTGRES].replace("purge_history", "forget_history")
    write_project(tmp_path, {POSTGRES: text})
    assert [line for _, line, _ in found(tmp_path, "STP-07")] == [15, 16]


# --- MOD-01: a call names a model role, never a model


def test_mod01_a_model_written_outside_the_fills_and_the_prices_is_a_finding(tmp_path):
    caller = (
        "from pydantic import Field\n\n\n"
        "class Kind:\n    model: str = 'claude-y'\n    other: str = Field(default='claude-z')\n\n\n"
        "def call(client, model='gpt-x'):\n"
        "    client.create(model='gpt-y', messages=[])\n"
        "    return {'model': 'gpt-z'}\n"
    )
    write_project(tmp_path, {f"{OM}/agents/kinds.py": caller})
    hits = found(tmp_path, "MOD-01")
    assert [line for _, line, _ in hits] == [5, 9, 10, 11]
    assert "names the model 'claude-y'; a call names a model role" in hits[0][2]


def test_mod01_the_sites_option_names_the_files_that_may_name_a_model(tmp_path):
    catalog = {f"{OM}/models/impl/catalog.py": "CATALOG = dict(model='claude-x')\n"}
    write_project(tmp_path, catalog)
    assert len(found(tmp_path, "MOD-01")) == 1
    sites = '\n[tool.agentic-check.options.MOD-01]\nsites = ["om/src/acme/om/models/impl/*.py"]\n'
    write_project(tmp_path, catalog, pyproject='[tool.agentic-check]\npackage = "acme"\n' + sites)
    assert found(tmp_path, "MOD-01") == []


# --- TOL-01: every tool declares its contract


def test_tol01_a_spec_without_its_mode_is_a_finding(tmp_path):
    write_project(tmp_path)
    assert found(tmp_path, "TOL-01") == []
    write_project(tmp_path, {TOOL: BASE[TOOL].replace("            mode=ToolMode.SYNC,\n", "")})
    hits = found(tmp_path, "TOL-01")
    assert len(hits) == 1
    assert "builds a ToolSpec without mode; every tool declares each by keyword" in hits[0][2]


def test_tol01_a_spec_built_from_a_mapping_is_left_to_the_review(tmp_path):
    spread = BASE[TOOL].replace('name="echo",', '**{"name": "echo"},')
    write_project(tmp_path, {TOOL: spread.replace("            mode=ToolMode.SYNC,\n", "")})
    assert found(tmp_path, "TOL-01") == []


# --- TOL-11: a secret's value never enters a step


def test_tol11_a_secret_typed_field_on_a_tool_input_output_or_step_is_a_finding(tmp_path):
    tool = (
        BASE[TOOL]
        .replace(
            "from acme.om.base import Platform\n",
            "from pydantic import SecretBytes, SecretStr\n\nfrom acme.om.base import Platform\n",
        )
        .replace(
            "class EchoInput(ToolInput):\n    text: str\n", "class EchoInput(ToolInput):\n    text: str\n    token: SecretStr\n"
        )
        .replace(
            "class EchoOutput(Platform):\n    text: str\n", "class EchoOutput(Platform):\n    key: SecretBytes | None = None\n"
        )
    )
    step = "from pydantic import SecretStr\n\nfrom acme.om.base import Platform\n\n\n"
    step += "class Step(Platform):\n    secret: 'SecretStr'\n"
    write_project(tmp_path, {TOOL: tool, f"{OM}/steps/types/step.py": step})
    hits = found(tmp_path, "TOL-11")
    assert sorted(m.split(" is a")[0] for _, _, m in hits) == ["EchoInput.token", "EchoOutput.key", "Step.secret"]


def test_tol11_a_secret_outside_what_a_step_carries_is_no_finding(tmp_path):
    settings = "from pydantic import BaseModel, SecretStr\n\n\nclass Settings(BaseModel):\n    api_key: SecretStr\n"
    write_project(tmp_path, {f"{OM}/budgets/settings.py": settings})
    assert found(tmp_path, "TOL-11") == []


# --- TOL-13: every tool runs through the transport


def test_tol13_a_tool_module_that_reaches_its_host_is_a_finding(tmp_path):
    tool = (
        "import os\nimport subprocess\nfrom asyncio import create_subprocess_exec\n\n"
        + BASE[TOOL]
        + "\n\ndef helper() -> None:\n    open('x').read()\n    os.system('ls')\n    create_subprocess_exec('ls')\n"
    )
    write_project(tmp_path, {TOOL: tool})
    hits = found(tmp_path, "TOL-13")
    assert [m.split(";")[0] for _, _, m in hits] == [
        "imports subprocess",
        "calls open",
        "calls os.system",
        "calls asyncio.create_subprocess_exec",
    ]


def test_tol13_a_module_that_defines_no_tool_may_start_a_process(tmp_path):
    write_project(tmp_path)
    assert found(tmp_path, "TOL-13") == []


# --- PRV-06: every dependency is wired by a root


def test_prv06_an_optional_or_defaulted_interface_in_an_engine_constructor_is_a_finding(tmp_path):
    manager = BASE[MANAGER].replace("gate: BudgetGateInterface)", "gate: BudgetGateInterface | None = None)")
    sink = (
        "from dataclasses import dataclass\nfrom typing import Optional\n\n"
        "from acme.om.budgets.gate import BudgetGateInterface\n\n\n"
        "class Meter:\n    def __init__(self, *, gate: Optional[BudgetGateInterface]) -> None:\n        self._gate = gate\n\n\n"
        "@dataclass(frozen=True)\nclass Wiring:\n    gate: BudgetGateInterface = None\n"
    )
    write_project(tmp_path, {MANAGER: manager, f"{OM}/budgets/impl/meter.py": sink})
    hits = found(tmp_path, "PRV-06")
    assert [m.split(";")[0] for _, _, m in hits] == [
        "BudgetsManagerImpl takes gate: BudgetGateInterface with a default",
        "Meter takes gate: BudgetGateInterface as optional",
        "Wiring takes gate: BudgetGateInterface with a default",
    ]


def test_prv06_a_root_and_a_memory_storage_may_default_an_interface(tmp_path):
    write_project(tmp_path)
    assert found(tmp_path, "PRV-06") == []
