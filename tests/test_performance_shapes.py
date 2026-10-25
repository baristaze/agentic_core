"""Every shape `performance/` commits holds numbers, booleans, and the
engine's tool classes alone, so no word, name, or path of the session it
was read from reaches the repository. And the classes a shape may name are
classes the engine declares."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PERFORMANCE = ROOT / "performance"
if str(PERFORMANCE) not in sys.path:
    sys.path.insert(0, str(PERFORMANCE))

from replay.shape import TOOL_CLASSES, ShapeRefused, check  # noqa: E402

SHAPES = sorted((PERFORMANCE / "shapes").glob("*.json"))
TOOL_SOURCE = ROOT / "scaffold" / "acme_root" / "om" / "src" / "acme" / "om" / "tools" / "types" / "tool.py"


def engine_classes() -> set[str]:
    """The values of the engine's `ToolClass`, read from its source."""
    tree = ast.parse(TOOL_SOURCE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "ToolClass":
            return {
                statement.value.value
                for statement in node.body
                if isinstance(statement, ast.Assign)
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            }
    raise AssertionError("the engine declares no ToolClass")


def strings(value: object, key: str = "") -> list[tuple[str, str]]:
    """Every string a JSON value holds, with the key it sits under."""
    if isinstance(value, str):
        return [(key, value)]
    if isinstance(value, dict):
        return [found for k, inner in value.items() for found in strings(inner, k)]
    if isinstance(value, list):
        return [found for inner in value for found in strings(inner, key)]
    return []


def test_a_shape_is_committed() -> None:
    assert SHAPES, "performance/shapes/ holds no shape"


def test_the_classes_a_shape_names_are_the_engines() -> None:
    assert set(TOOL_CLASSES) <= engine_classes()


@pytest.mark.parametrize("path", SHAPES, ids=[path.name for path in SHAPES])
def test_a_committed_shape_holds_numbers_booleans_and_tool_classes_alone(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    turns = check(data)
    assert turns
    assert all(key == "tool" and value in TOOL_CLASSES for key, value in strings(data))


@pytest.mark.parametrize(
    "bent",
    [
        {"turns": [{"blocks": [{"text": "a word"}]}]},
        {"turns": [{"blocks": [{"tool": "Bash", "input": 1, "output": 1, "failed": False, "took_ms": 0}]}]},
        {"turns": [{"blocks": [{"tool": "read", "input": 1, "output": 1, "failed": "no", "took_ms": 0}]}]},
        {"turns": [{"blocks": [{"tool": "read", "input": 1, "output": 1, "failed": False, "took_ms": 0, "path": 1}]}]},
        {"turns": [{"blocks": [{"text": 3}], "name": 1}]},
        {"turns": [{"blocks": [{"text": 3}], "person": "hello"}]},
        {"turns": [{"blocks": [{"thinking": 3, "signature": "opaque"}]}]},
        {"turns": [{"blocks": [{"text": True}]}]},
        {"turns": [{"blocks": [{"text": -1}]}]},
        {"turns": [{"blocks": []}], "source": "a path"},
        {"turns": []},
    ],
)
def test_anything_but_the_schema_is_refused(bent: object) -> None:
    with pytest.raises(ShapeRefused):
        check(bent)
