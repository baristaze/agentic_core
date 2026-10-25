"""Reads one coding agent's session transcript and writes its shape.

The transcript is JSON lines. A model turn is every `assistant` line that
shares one message id, a block to a line; a `user` line carries the tool
results, by the id of the call they answer, or what the person said. The
shape keeps, for each turn, the size of each block in the order written
(a thinking block's with its signature's),
each tool call's class and the sizes of its input and output, whether it
failed, how long it took, and the gap before the turn. It keeps no word,
no name, no id, and no path: a tool's name is read only to give its
class, by the rule in `tool_class`, and is dropped.

The transcript keeps no thinking text. A turn's thinking size is its
thinking tokens, as its usage reports them, or else its output tokens
less its visible ones, in characters at the transcript's own rate of
characters per visible token.

    python3 capture.py <transcript.jsonl> shapes/<name>.json

It prints counts alone, never a value it read."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from replay.shape import check, dump

EXECUTE = frozenset({"Bash", "BashOutput", "KillShell", "KillBash", "Monitor", "TaskOutput", "TaskStop", "PowerShell"})
WRITE = frozenset({"Edit", "MultiEdit", "Write", "NotebookEdit"})
SPAWN = frozenset({"Agent", "Task", "SendMessage"})
NETWORK = frozenset({"WebFetch", "WebSearch"})
INTEGRATION_PREFIX = "mcp__"


def tool_class(name: str) -> str:
    """The engine's class for a tool, by what it does: a command runs code,
    an edit changes files, an agent starts a session, a fetch reaches the
    network, a connector's tool acts on a bound system, and the rest read."""
    if name.startswith(INTEGRATION_PREFIX):
        return "integration"
    if name in EXECUTE:
        return "execute"
    if name in WRITE:
        return "write"
    if name in SPAWN:
        return "spawn"
    if name in NETWORK:
        return "network"
    return "read"


def at(line: dict[str, object]) -> float:
    stamp = line.get("timestamp")
    if not isinstance(stamp, str):
        return 0.0
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()


def size_of(content: object) -> int:
    """A tool result's size in characters: its text, or each block's text,
    and an image as its encoded length."""
    if isinstance(content, str):
        return len(content)
    total = 0
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if isinstance(block.get("text"), str):
                total += len(block["text"])
            source = block.get("source")
            if isinstance(source, dict) and isinstance(source.get("data"), str):
                total += len(source["data"])
    return total


def text_of(content: object) -> int:
    """What the person said, in characters: a string, or its text blocks."""
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(
            len(b["text"]) for b in content if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
        )
    return 0


@dataclass
class Call:
    tool_class: str
    input: int
    started: float
    output: int = 0
    failed: bool = False
    ended: float | None = None


@dataclass
class Model:
    started: float
    after: float
    person: int
    blocks: list[tuple[str, object]] = field(default_factory=list)
    output_tokens: int = 0
    thinking_tokens: int = 0


def read(path: Path) -> tuple[list[Model], dict[str, Call], dict[str, int]]:
    models: dict[str, Model] = {}
    calls: dict[str, Call] = {}
    counts = {"lines": 0, "unreadable": 0, "orphan_results": 0, "person_messages": 0}
    last_event = 0.0
    said = 0
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            counts["lines"] += 1
            try:
                line = json.loads(raw)
            except json.JSONDecodeError:
                counts["unreadable"] += 1
                continue
            message = line.get("message")
            if not isinstance(message, dict):
                continue
            when = at(line)
            if line.get("type") == "assistant":
                key = str(message.get("id"))
                if key not in models:
                    models[key] = Model(started=when, after=last_event, person=said)
                    said = 0
                model = models[key]
                usage: dict[str, Any] = message["usage"] if isinstance(message.get("usage"), dict) else {}
                model.output_tokens = max(model.output_tokens, int(usage.get("output_tokens") or 0))
                details = usage.get("output_tokens_details")
                if isinstance(details, dict):
                    model.thinking_tokens = max(model.thinking_tokens, int(details.get("thinking_tokens") or 0))
                for block in message.get("content") or ():
                    if not isinstance(block, dict):
                        continue
                    kind = block.get("type")
                    if kind == "thinking":
                        signature = block.get("signature")
                        model.blocks.append(("thinking", len(signature) if isinstance(signature, str) else 0))
                    elif kind == "text" and isinstance(block.get("text"), str):
                        model.blocks.append(("text", len(block["text"])))
                    elif kind == "tool_use":
                        call_id = str(block.get("id"))
                        size = len(json.dumps(block.get("input"), separators=(",", ":"), sort_keys=True))
                        calls[call_id] = Call(tool_class(str(block.get("name"))), size, when)
                        model.blocks.append(("tool", call_id))
                last_event = when
            elif line.get("type") == "user":
                content = message.get("content")
                results = (
                    [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
                    if isinstance(content, list)
                    else []
                )
                for result in results:
                    call = calls.get(str(result.get("tool_use_id")))
                    if call is None:
                        counts["orphan_results"] += 1
                        continue
                    call.output = size_of(result.get("content"))
                    call.failed = bool(result.get("is_error"))
                    call.ended = when
                if not results:
                    spoken = text_of(content)
                    if spoken:
                        said += spoken
                        counts["person_messages"] += 1
                last_event = when
    return list(models.values()), calls, counts


def shape(models: list[Model], calls: dict[str, Call]) -> tuple[list[dict[str, object]], dict[str, float]]:
    visible_chars = 0
    visible_tokens = 0
    for model in models:
        if not model.thinking_tokens and not any(kind == "thinking" for kind, _ in model.blocks):
            visible_chars += sum(_visible(kind, value, calls) for kind, value in model.blocks)
            visible_tokens += model.output_tokens
    rate = visible_chars / visible_tokens if visible_tokens else 4.0
    turns: list[dict[str, object]] = []
    for model in models:
        visible = sum(_visible(kind, value, calls) for kind, value in model.blocks)
        thinking_tokens = model.thinking_tokens or max(0, model.output_tokens - round(visible / rate))
        thinking = round(thinking_tokens * rate)
        blocks: list[dict[str, object]] = []
        signed = sum(int(value) for kind, value in model.blocks if kind == "thinking" and isinstance(value, int))
        thought = False
        for kind, value in model.blocks:
            if kind == "thinking":
                if not thought:
                    blocks.append({"thinking": thinking, "signature": signed})
                    thought = True
            elif kind == "text":
                blocks.append({"text": value})
            else:
                call = calls[str(value)]
                ended = call.ended if call.ended is not None else call.started
                blocks.append(
                    {
                        "tool": call.tool_class,
                        "input": call.input,
                        "output": call.output,
                        "failed": call.failed,
                        "took_ms": max(0, round((ended - call.started) * 1000)),
                    }
                )
        turn: dict[str, object] = {"gap_ms": max(0, round((model.started - model.after) * 1000)), "blocks": blocks}
        if model.person:
            turn["person"] = model.person
        turns.append(turn)
    return turns, {"chars_per_visible_token": round(rate, 3)}


def _visible(kind: str, value: object, calls: dict[str, Call]) -> int:
    if kind == "text" and isinstance(value, int):
        return value
    if kind == "tool":
        return calls[str(value)].input
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("transcript", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args(argv)
    models, calls, counts = read(args.transcript)
    turns, rates = shape(models, calls)
    check({"turns": turns})
    args.out.write_text(dump(turns), encoding="utf-8")
    classes: dict[str, int] = {}
    for call in calls.values():
        classes[call.tool_class] = classes.get(call.tool_class, 0) + 1
    runs = sum(1 for turn in turns if not any("tool" in block for block in cast(list[dict[str, object]], turn["blocks"])))
    print(
        json.dumps(
            {
                **counts,
                "turns": len(turns),
                "runs": runs,
                "tool_calls": len(calls),
                "unanswered_calls": sum(1 for call in calls.values() if call.ended is None),
                "classes": classes,
                **rates,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
