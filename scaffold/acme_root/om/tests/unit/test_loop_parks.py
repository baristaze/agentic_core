"""How a run stops and waits: a cancelled loop starts no loop on the input
it left, and a pause parks at the next safe point until a resume."""

import asyncio
from pathlib import Path

from acme.om.agent_sessions.types.agent_session import AgentSessionStatus
from acme.om.agent_steps.types.agent_step import Actor, AgentStep, AgentStepType, Origin
from acme.om.agent_steps.types.header import (
    ControlCommand,
    ControlHeader,
    LoopOutcome,
    ParkReason,
    ToolFailure,
)
from acme.om.agents.types.run import RunEnd
from acme.om.base import new_id

from contracts.loops import (
    control,
    loop_over,
    of_type,
    reply,
    said,
    use,
)


async def test_a_cancelled_loop_starts_no_new_loop_on_a_message_it_left_undelivered(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "Find the total.")
    loop.anthropic.add(reply(use("slow", use_id="use_slow")))
    slow = loop.tools["slow"]
    running = asyncio.ensure_future(loop.loops.run(loop.owner, agent_session_id))
    await slow.started.wait()
    await loop.say(agent_session_id, "Also the average.")
    cancel = AgentStep(
        id=new_id(),
        created_at=loop.clock(),
        agent_session_id=agent_session_id,
        loop_id=new_id(),
        type=AgentStepType.CONTROL,
        actor=Actor.PERSON,
        origin=Origin.PORTAL,
        header=ControlHeader(command=ControlCommand.CANCEL),
    )
    await loop.managers.agent_steps.append_inputs(loop.owner, agent_session_id, [cancel])

    run = await running

    assert run.outcome is LoopOutcome.CANCELLED
    answer = of_type(await loop.history(agent_session_id), AgentStepType.TOOL_RESPONSE)[-1]
    assert getattr(answer.header, "failure", None) is ToolFailure.INTERRUPTED
    session = await loop.managers.agent_sessions.get_agent_session(loop.owner, agent_session_id)
    assert session.status is AgentSessionStatus.IDLE
    again = await loop.loops.run(loop.owner, agent_session_id)
    assert again.end is RunEnd.IDLE and len(loop.anthropic.calls) == 1


async def test_a_pause_parks_at_the_next_safe_point_and_a_resume_takes_the_loop_up(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "Find the total.")
    loop.anthropic.add(reply(use("slow", use_id="use_slow")), reply(said("It is 12.")))
    slow = loop.tools["slow"]
    running = asyncio.ensure_future(loop.loops.run(loop.owner, agent_session_id))
    await slow.started.wait()
    await loop.managers.agent_steps.append_inputs(
        loop.owner, agent_session_id, [control(loop, agent_session_id, ControlCommand.PAUSE)]
    )
    slow.release.set()

    paused = await running

    assert paused.end is RunEnd.PARKED and paused.park is not None
    assert paused.park.reason is ParkReason.PAUSE and len(loop.anthropic.calls) == 1
    await loop.managers.agent_steps.append_inputs(
        loop.owner, agent_session_id, [control(loop, agent_session_id, ControlCommand.RESUME)]
    )
    resumed = await loop.loops.run(loop.owner, agent_session_id)
    assert resumed.outcome is LoopOutcome.SUCCEEDED
