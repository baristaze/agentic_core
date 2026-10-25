"""The replay's script: the shape's turns made into the scripted twin's
replies, with synthetic words of the shape's sizes.

The shape is played in laps: from its first turn to its last, and from the
first again, until the replay holds the turns it was asked for. A turn that
calls no tool ends a run of the loop, as the session's did; the next run
opens with what the person said. The last turn always ends a run.

Everything is made before the clock starts, so the twin streams a reply
it already holds, and the engine is not timed making the words."""

from __future__ import annotations

import json
from dataclasses import dataclass

from acme.integrations.model_providers.calls import ModelReply
from acme.integrations.model_providers.content import TextBlock, ThinkingBlock, ThinkingSource, ToolUseBlock
from acme.integrations.model_providers.types import ProviderName, StopReason, Usage
from replay.shape import Text, Thinking, Tool, Turn
from replay.tools import tool_name
from replay.words import Words

PROVIDER = ProviderName.ANTHROPIC
INPUT_FRAME = len(json.dumps({"failed": False, "size": 0, "text": ""}, separators=(",", ":")))
"""What a call's input holds beside its words, so the input is the shape's
size."""
CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class Run:
    """One run of the loop: what the person says, and the replies the model
    gives until one calls no tool."""

    said: str
    replies: tuple[ModelReply, ...]


def runs(shape: tuple[Turn, ...], turns: int, words: Words, model: str) -> list[Run]:
    played: list[Run] = []
    replies: list[ModelReply] = []
    said: str | None = None
    for index in range(turns):
        turn = shape[index % len(shape)]
        last = index == turns - 1
        if said is None:
            said = words.text(max(turn.person, 1))
        reply = _reply(turn, words, model, index, end=last or not turn.tools)
        replies.append(reply)
        if reply.stop_reason is StopReason.END_TURN:
            played.append(Run(said=said, replies=tuple(replies)))
            replies, said = [], None
    return played


def _reply(turn: Turn, words: Words, model: str, index: int, *, end: bool) -> ModelReply:
    blocks: list[TextBlock | ToolUseBlock] = []
    thinking: list[ThinkingBlock] = []
    calls = 0
    for block in turn.blocks:
        if isinstance(block, Thinking):
            thinking.append(
                ThinkingBlock(
                    text=words.text(block.size),
                    signature=words.text(max(block.signature, 1)),
                    source=_source(model),
                    at=len(blocks),
                )
            )
        elif isinstance(block, Text):
            blocks.append(TextBlock(text=words.text(block.size)))
        elif isinstance(block, Tool) and not end:
            blocks.append(
                ToolUseBlock(
                    id=f"call_{index}_{calls}",
                    name=tool_name(block.tool_class),
                    input={
                        "text": words.text(max(1, block.input - INPUT_FRAME)),
                        "size": block.output,
                        "failed": block.failed,
                    },
                )
            )
            calls += 1
    if end and not any(isinstance(block, TextBlock) for block in blocks):
        blocks.append(TextBlock(text=words.text(1)))
    said = sum(len(b.text) if isinstance(b, TextBlock) else len(json.dumps(dict(b.input))) for b in blocks)
    return ModelReply(
        blocks=tuple(blocks),
        thinking=tuple(thinking),
        stop_reason=StopReason.END_TURN if end else StopReason.TOOL_USE,
        usage=Usage(
            input=1,
            output=max(1, said // CHARS_PER_TOKEN),
            thinking=sum(len(t.text) for t in thinking) // CHARS_PER_TOKEN,
        ),
        model=model,
    )


def _source(model: str) -> ThinkingSource:
    return ThinkingSource(provider=PROVIDER, model=model)
