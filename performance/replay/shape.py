"""A session's shape: what a long session did, kept as numbers alone.

A shape is a list of model turns, in order. A turn may open with what the
person said, as its size; then it holds its blocks in the order the model
wrote them: thinking, text, and tool calls, each as its size in
characters. A tool call keeps its class, the sizes of its input and its
output, whether it failed, and how long it took. A turn keeps the gap
before it. Nothing else is kept: no word, no name, no id, and no path.

`check` holds a shape to that schema. Its values are numbers, booleans,
and the engine's tool classes alone, so no other string passes.

This module imports nothing from the engine, so the capture and the
tests read it on any Python."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

TOOL_CLASSES = ("read", "write", "execute", "network", "integration", "spawn")
"""The engine's tool classes a shape may name: the ones a coding agent's
tools fall in. The replay holds each to the engine's own `ToolClass`."""

TURN_KEYS = frozenset({"person", "gap_ms", "blocks"})
THINKING_KEYS = frozenset({"thinking", "signature"})
TEXT_KEYS = frozenset({"text"})
TOOL_KEYS = frozenset({"tool", "input", "output", "failed", "took_ms"})


class ShapeRefused(ValueError):
    """A shape that holds anything but the schema's numbers, booleans, and
    tool classes."""


@dataclass(frozen=True)
class Thinking:
    size: int
    signature: int


@dataclass(frozen=True)
class Text:
    size: int


@dataclass(frozen=True)
class Tool:
    tool_class: str
    input: int
    output: int
    failed: bool
    took_ms: int


@dataclass(frozen=True)
class Turn:
    person: int
    gap_ms: int
    blocks: tuple[Thinking | Text | Tool, ...]

    @property
    def tools(self) -> tuple[Tool, ...]:
        return tuple(block for block in self.blocks if isinstance(block, Tool))


def _count(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ShapeRefused(f"{where} is not a count")
    return value


def _block(raw: object, where: str) -> Thinking | Text | Tool:
    if not isinstance(raw, dict):
        raise ShapeRefused(f"{where} is not an object")
    keys = frozenset(raw)
    if keys == THINKING_KEYS:
        return Thinking(_count(raw["thinking"], f"{where}.thinking"), _count(raw["signature"], f"{where}.signature"))
    if keys == TEXT_KEYS:
        return Text(_count(raw["text"], f"{where}.text"))
    if keys != TOOL_KEYS:
        raise ShapeRefused(f"{where} holds keys outside the schema")
    tool_class = raw["tool"]
    if not isinstance(tool_class, str) or tool_class not in TOOL_CLASSES:
        raise ShapeRefused(f"{where}.tool is not a tool class")
    if not isinstance(raw["failed"], bool):
        raise ShapeRefused(f"{where}.failed is not a boolean")
    return Tool(
        tool_class=tool_class,
        input=_count(raw["input"], f"{where}.input"),
        output=_count(raw["output"], f"{where}.output"),
        failed=raw["failed"],
        took_ms=_count(raw["took_ms"], f"{where}.took_ms"),
    )


def check(data: object) -> tuple[Turn, ...]:
    """The shape's turns, or `ShapeRefused` naming the first place that
    holds anything but the schema."""
    if not isinstance(data, dict) or frozenset(data) != {"turns"}:
        raise ShapeRefused("a shape is an object of turns alone")
    raw_turns = data["turns"]
    if not isinstance(raw_turns, list) or not raw_turns:
        raise ShapeRefused("a shape holds at least one turn")
    turns: list[Turn] = []
    for index, raw in enumerate(raw_turns):
        where = f"turns[{index}]"
        if not isinstance(raw, dict) or not frozenset(raw) <= TURN_KEYS or "blocks" not in raw:
            raise ShapeRefused(f"{where} holds keys outside the schema")
        blocks = raw["blocks"]
        if not isinstance(blocks, list):
            raise ShapeRefused(f"{where}.blocks is not a list")
        turns.append(
            Turn(
                person=_count(raw.get("person", 0), f"{where}.person"),
                gap_ms=_count(raw.get("gap_ms", 0), f"{where}.gap_ms"),
                blocks=tuple(_block(block, f"{where}.blocks[{i}]") for i, block in enumerate(blocks)),
            )
        )
    return tuple(turns)


def load(path: Path) -> tuple[Turn, ...]:
    return check(json.loads(path.read_text(encoding="utf-8")))


def dump(turns: list[dict[str, object]]) -> str:
    """A shape as the folder commits it: one turn a line, so a diff reads."""
    lines = ",\n".join(json.dumps(turn, separators=(",", ":")) for turn in turns)
    return '{"turns": [\n' + lines + "\n]}\n"
