"""What `make performance-check` holds, on memory storage and the key twin:
a short replay whose every step reads back equal to what was said, and
whose stored steps and tool outputs hold sealed content alone.

- Each model response reads back as the reply the twin streamed, block by
  block; each tool response as the output its tool returned; each message
  as what the person said.
- Below the sealing layer, every step that says something is sealed, none
  holds a block in the clear, and none holds the run's marker. No file the
  object store keeps holds it either, and at least one tool output was
  kept, so the scan reads something.
- The harness measures a path where things are sealed: the key service
  was called for the steps written."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from acme.integrations.model_providers.calls import ModelReply
from acme.om.agent_steps.types.agent_step import AgentStep, AgentStepType
from acme.om.agent_steps.types.content import TextBlock, ToolResultBlock
from replay.engine import Engine
from replay.generate import runs
from replay.run import point
from replay.shape import Turn
from replay.tools import SyntheticTool
from replay.words import Words, marker

CHECK_TURNS = 40
SEED = 1


class CheckFailed(AssertionError):
    pass


def expect(holds: bool, what: str) -> None:
    if not holds:
        raise CheckFailed(what)
    print(f"  ok  {what}")


def said_of(step: AgentStep) -> object:
    """A model response's blocks and its thinking, which it keeps apart."""
    return (
        [block.model_dump(mode="json") for block in step.content.blocks],
        [block.model_dump(mode="json") for block in step.children.thinking],
    )


def replied(reply: ModelReply) -> object:
    return (
        [block.model_dump(mode="json") for block in reply.blocks],
        [block.model_dump(mode="json") for block in reply.thinking],
    )


async def check_memory(shape: tuple[Turn, ...], folder: Path) -> int:
    mark = marker()
    words = Words(SEED, mark)
    engine = Engine.on_memory(folder / "infra", words)
    engine.twin.streamed = {}
    answers: list[str] = []
    SyntheticTool.record = answers
    script = runs(shape, CHECK_TURNS, words, model="claude-sonnet-5-5")
    await engine.start()
    try:
        measured, (agent_session_id,) = await point(engine, [script], profiled=False)
        print(f"replayed {measured.turns} turns: {measured.steps_written} steps written, {measured.key_calls} key calls")
        history = await engine.history(agent_session_id)
        streamed = engine.twin.streamed[agent_session_id]
        responses = [step for step in history if step.type is AgentStepType.MODEL_RESPONSE]
        expect(len(responses) == len(streamed) == CHECK_TURNS, f"{CHECK_TURNS} model responses read back, one per reply streamed")
        expect(
            all(said_of(step) == replied(reply) for step, reply in zip(responses, streamed, strict=True)),
            "each model response reads back as the reply the twin streamed",
        )
        results = [
            await _result_text(engine, agent_session_id, step) for step in history if step.type is AgentStepType.TOOL_RESPONSE
        ]
        expect(len(results) == len(answers) > 0, "one tool response read back for each tool call answered")
        expect(
            all(_answered(text, failed, answer) for (text, failed), answer in zip(results, answers, strict=True)),
            "each tool response reads back as its tool's output, a kept one whole",
        )
        messages = [step.as_text() for step in history if step.type is AgentStepType.MESSAGE]
        expect(messages == [run.said for run in script], "each message reads back as what the person said")

        stored = await engine.storage.get_agent_step_storage().read_agent_steps(engine.ctx().org_id, agent_session_id, 0, 100_000)
        expect(len(stored) == len(history), "the storage below the sealing layer holds every step")
        speaking = [step for step in stored if step.content.is_sealed()]
        expect(all(not step.content.blocks for step in stored), "no stored step holds a block in the clear")
        expect(
            len(speaking) == sum(1 for step in history if step.content.blocks), "every step that says something is stored sealed"
        )
        expect(not any(mark in step.model_dump_json() for step in stored), "no stored step holds the run's marker")
        kept = [path for path in (folder / "infra").rglob("*") if path.is_file() and "tool-outputs" in path.parts]
        expect(bool(kept), f"tool outputs were kept in the object store ({len(kept)})")
        expect(not any(mark.encode() in path.read_bytes() for path in kept), "no tool output kept holds the run's marker")
        expect(measured.key_calls > 0, "the key service was called for the steps written")
    finally:
        SyntheticTool.record = None
        await engine.close()
    return 0


async def _result_text(engine: Engine, agent_session_id: UUID, step: AgentStep) -> tuple[str, bool]:
    """A tool response's text and whether it failed: a kept tool output's
    whole text, read back through the engine, or the step's own."""
    (block,) = (b for b in step.content.blocks if isinstance(b, ToolResultBlock))
    kept = getattr(step.header, "tool_output", None)
    if kept is None:
        return "".join(part.text for part in block.parts if isinstance(part, TextBlock)), block.is_error
    text, offset = "", 0
    while True:
        page = await engine.managers.windows.get_tool_output(engine.ctx(), agent_session_id, kept.id, offset, kept.characters)
        text += page.text
        offset += len(page.text)
        if not page.has_more or not page.text:
            return text, block.is_error


def _answered(text: str, failed: bool, answer: str) -> bool:
    if failed:
        return answer in text
    try:
        return json.loads(text) == {"text": answer}
    except ValueError:
        return False
