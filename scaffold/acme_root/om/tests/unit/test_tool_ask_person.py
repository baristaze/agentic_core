"""`ask_person` through the loop: the agent asks, the loop parks on `person`
with the question in the history before any further model call, and the
person's next message is the answer that resumes it. Nothing but an answer
lets the loop call the model again, and a question the model wrote that
its schema refuses parks nothing."""

import json
from pathlib import Path

from contracts.loops import ASSISTANT, DELIVERY, HELPER, Loop, call, loop_over, reply, said

from acme.integrations.model_providers.calls import ModelCall
from acme.om.agent_sessions.rules import QUESTION
from acme.om.agent_sessions.types.agent_session import SessionStatus
from acme.om.agents.types.run import RunEnd
from acme.om.base import new_id, utcnow
from acme.om.steps.rules import control_step
from acme.om.steps.types.content import TextBlock, ToolResultBlock
from acme.om.steps.types.header import (
    ControlCommand,
    LoopOutcome,
    ParkReason,
    ToolFailure,
    ToolResponseHeader,
)
from acme.om.steps.types.step import Step, StepType


def helper(tmp_path: Path) -> Loop:
    return loop_over(tmp_path, kinds=(ASSISTANT, DELIVERY, HELPER))


def text_of(model_call: ModelCall) -> str:
    texts: list[str] = []
    for message in model_call.messages:
        for block in message.blocks:
            parts = block.parts if isinstance(block, ToolResultBlock) else (block,)
            texts.extend(part.text for part in parts if isinstance(part, TextBlock))
    return "\n".join(texts)


def answers(steps: list[Step]) -> list[Step]:
    return [step for step in steps if step.type is StepType.TOOL_RESPONSE]


def failure_of(answer: Step) -> ToolFailure | None:
    assert isinstance(answer.header, ToolResponseHeader)
    return answer.header.failure


async def test_a_question_parks_on_person_before_any_model_call_and_the_answer_resumes_it(
    tmp_path: Path,
) -> None:
    loop = helper(tmp_path)
    session_id = await loop.start("helper")
    await loop.say(session_id, "Fix the weekly report.")
    loop.anthropic.add(
        reply(said("Which week?"), call("ask_person", question="Which week's report is wrong?")),
        reply(said("The report for week 12 is fixed.")),
    )

    run = await loop.loops.run(loop.owner, session_id)

    assert run.end is RunEnd.PARKED and run.park == QUESTION
    assert run.park is not None and (run.park.reason, run.park.unlock) == (
        ParkReason.PERSON,
        "answer",
    )
    assert len(loop.anthropic.calls) == 1, "no model call while the question waits"
    steps = await loop.history(session_id)
    (asked,) = answers(steps)
    assert failure_of(asked) is None
    shown = asked.as_tool_response().parts[0]
    assert isinstance(shown, TextBlock)
    assert json.loads(shown.text) == {"question": "Which week's report is wrong?"}
    assert steps[-1].type is StepType.PARKED and asked.seq < steps[-1].seq
    session = await loop.managers.agent_sessions.get_session(loop.owner, session_id)
    assert (session.status, session.park) == (SessionStatus.PARKED, QUESTION)

    idle = await loop.loops.run(loop.owner, session_id)
    assert idle.end is RunEnd.IDLE and len(loop.anthropic.calls) == 1, "nothing runs unanswered"

    answer = await loop.say(session_id, "Week 12.")
    resumed = await loop.loops.run(loop.owner, session_id)

    assert resumed.outcome is LoopOutcome.SUCCEEDED and len(loop.anthropic.calls) == 2
    assert resumed.loop_id == run.loop_id, "the answer resumes the loop that asked"
    last = loop.anthropic.calls[1].messages[-1]
    assert last.role == "user" and "Week 12." in text_of(loop.anthropic.calls[1])
    steps = await loop.history(session_id)
    (second,) = [s for s in steps if s.type is StepType.MODEL_REQUEST and s.seq > answer.seq]
    assert answer.id in second.refs, "the next request delivers the answer as its input"


async def test_an_unlock_that_is_no_answer_parks_the_question_again_with_no_model_call(
    tmp_path: Path,
) -> None:
    loop = helper(tmp_path)
    session_id = await loop.start("helper")
    await loop.say(session_id, "Fix the weekly report.")
    loop.anthropic.add(reply(call("ask_person", question="Which week?")), reply(said("Done.")))
    await loop.loops.run(loop.owner, session_id)
    unlock = control_step(new_id(), utcnow(), session_id, loop.owner, ControlCommand.UNLOCK, None)
    await loop.managers.steps.append_inputs(loop.owner, session_id, [unlock])

    again = await loop.loops.run(loop.owner, session_id)

    assert again.end is RunEnd.PARKED and again.park == QUESTION
    assert len(loop.anthropic.calls) == 1, "the history says the question still waits"


async def test_a_question_its_schema_refuses_parks_nothing_and_the_model_reads_why(
    tmp_path: Path,
) -> None:
    loop = helper(tmp_path)
    session_id = await loop.start("helper")
    await loop.say(session_id, "Fix the weekly report.")
    loop.anthropic.add(
        reply(call("ask_person", question="", urgent=True)), reply(said("I will go on."))
    )

    run = await loop.loops.run(loop.owner, session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED and len(loop.anthropic.calls) == 2
    (refused,) = answers(await loop.history(session_id))
    assert failure_of(refused) is ToolFailure.INVALID_INPUT
    assert "question" in text_of(loop.anthropic.calls[1])
