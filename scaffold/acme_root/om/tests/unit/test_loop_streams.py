"""A stream's open and close, over the memory storage and the scripted
provider: the loop tells the carrier a model stream and a tool call's
stream open before their first part and complete after their last, once
the step is stored, and also when the call fails. A carrier that fails to
hear either costs the live view alone: the loop goes on."""

from pathlib import Path
from uuid import UUID

import pytest
from contracts.loops import Loop, loop_over, reply, said, use

from acme.integrations.model_providers.scripted import ScriptedFailure
from acme.integrations.model_providers.types import ErrorKind
from acme.om.agents.impl.sink import StreamSinkMemoryImpl
from acme.om.context import TenantContext
from acme.om.steps.types.header import LoopOutcome
from acme.om.steps.types.step import Step, StepType
from acme.om.steps.types.stream import StreamPart
from acme.om.tools.tool import ToolRuntime
from acme.om.tools.types.tool import ToolInput

OPENED, PART, COMPLETED = "opened", "part", "completed"


class Told(StreamSinkMemoryImpl):
    """A carrier that keeps, in order, each stream it was told opened, each
    part, and each stream it was told completed, by the step each names."""

    def __init__(self) -> None:
        super().__init__()
        self.told: list[tuple[str, UUID]] = []

    def emit(self, part: StreamPart) -> None:
        super().emit(part)
        self.told.append((PART, part.step_id))

    def opened(self, ctx: TenantContext, session_id: UUID, step_id: UUID) -> None:
        self.told.append((OPENED, step_id))

    def completed(self, ctx: TenantContext, session_id: UUID, step_id: UUID) -> None:
        self.told.append((COMPLETED, step_id))

    def of(self, step_id: UUID) -> list[str]:
        return [what for what, told in self.told if told == step_id]


class Deaf(StreamSinkMemoryImpl):
    """A carrier that fails whenever it is told a stream opened or completed."""

    def opened(self, ctx: TenantContext, session_id: UUID, step_id: UUID) -> None:
        raise ConnectionError("the carrier is down")

    def completed(self, ctx: TenantContext, session_id: UUID, step_id: UUID) -> None:
        raise ConnectionError("the carrier is down")


def of_type(steps: list[Step], step_type: StepType) -> list[Step]:
    return [step for step in steps if step.type is step_type]


def opened_then_completed(told: list[str]) -> bool:
    """One open first, one completion last, and parts only between them."""
    return (
        len(told) >= 2 and told[0] == OPENED and told[-1] == COMPLETED and set(told[1:-1]) <= {PART}
    )


async def asked(loop: Loop, text: str = "What is the total?") -> UUID:
    session_id = await loop.start()
    await loop.say(session_id, text)
    return session_id


async def test_a_model_stream_and_a_tool_call_open_before_their_parts_and_complete_after(
    tmp_path: Path,
) -> None:
    sink = Told()
    loop = loop_over(tmp_path, sink=sink)
    session_id = await asked(loop)
    loop.anthropic.add(reply(use("lookup")), reply(said("The total is 12.")))

    run = await loop.loops.run(loop.owner, session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED
    steps = await loop.history(session_id)
    responses = of_type(steps, StepType.MODEL_RESPONSE)
    (answer,) = of_type(steps, StepType.TOOL_RESPONSE)
    assert len(responses) == 2
    for step in (*responses, answer):
        assert opened_then_completed(sink.of(step.id)), (step.type, sink.of(step.id))
    assert PART in sink.of(responses[-1].id), "the model's text streamed between them"
    streams = [told for what, told in sink.told if what == OPENED]
    assert streams == [responses[0].id, answer.id, responses[1].id], "each in its turn"


async def test_a_model_call_that_fails_still_completes_its_stream(tmp_path: Path) -> None:
    sink = Told()
    loop = loop_over(tmp_path, sink=sink)
    session_id = await asked(loop)
    broken = reply(said("The total"))
    loop.anthropic.add(ScriptedFailure(kind=ErrorKind.PERMANENT, partial=broken))

    run = await loop.loops.run(loop.owner, session_id)

    assert run.outcome is LoopOutcome.ERRORED
    (closing,) = of_type(await loop.history(session_id), StepType.MODEL_RESPONSE)
    told = sink.of(closing.id)
    assert opened_then_completed(told) and PART in told, told


async def test_a_tool_call_that_fails_still_completes_its_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink = Told()
    loop = loop_over(tmp_path, sink=sink)
    lookup = loop.tools["lookup"]

    async def broken(ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime) -> None:
        raise RuntimeError("the lookup broke")

    monkeypatch.setattr(lookup, "run", broken)
    session_id = await asked(loop)
    loop.anthropic.add(reply(use("lookup")), reply(said("It failed.")))

    await loop.loops.run(loop.owner, session_id)

    (answer,) = of_type(await loop.history(session_id), StepType.TOOL_RESPONSE)
    assert answer.as_tool_response().is_error, "the call failed"
    assert opened_then_completed(sink.of(answer.id)), sink.of(answer.id)


async def test_a_carrier_that_fails_to_hear_a_stream_open_or_complete_stops_nothing(
    tmp_path: Path,
) -> None:
    sink = Deaf()
    loop = loop_over(tmp_path, sink=sink)
    session_id = await asked(loop)
    loop.anthropic.add(reply(use("lookup")), reply(said("The total is 12.")))

    run = await loop.loops.run(loop.owner, session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED
    assert loop.tools["lookup"].ran_as == [loop.owner.user_id], "the tool ran"
    steps = await loop.history(session_id)
    assert len(of_type(steps, StepType.MODEL_RESPONSE)) == 2
    assert sink.parts, "the parts still reached the carrier"
