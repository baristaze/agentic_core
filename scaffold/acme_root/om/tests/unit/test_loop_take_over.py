"""Taking a loop's environment over fences every run, and giving it back
tells the model what changed."""

import asyncio
from pathlib import Path

from contracts.loops import (
    loop_over,
    of_type,
    reply,
    said,
    text_of,
    use,
)

from acme.om.agent_sessions.types.agent_session import AgentSessionStatus
from acme.om.agent_steps.types.agent_step import AgentStepType
from acme.om.agent_steps.types.header import (
    LoopOutcome,
    ParkReason,
)
from acme.om.agents.types.run import RunEnd


async def test_taking_over_fences_the_run_and_giving_back_tells_the_model_what_changed(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "Find the total.")
    loop.anthropic.add(reply(use("slow", use_id="use_slow")), reply(said("I see your change.")))
    slow = loop.tools["slow"]
    running = asyncio.ensure_future(loop.loops.run(loop.owner, agent_session_id))
    await slow.started.wait()

    held = await loop.loops.take_over(loop.owner, agent_session_id)
    slow.release.set()
    fenced = await running

    assert held.park is not None and held.park.reason is ParkReason.HANDOVER
    assert fenced.end is RunEnd.STALE, "the agent stands down"
    assert (await loop.loops.run(loop.owner, agent_session_id)).end is RunEnd.IDLE
    given = await loop.loops.give_back(loop.owner, agent_session_id, "I fixed the total by hand.")
    assert given.status is AgentSessionStatus.PENDING
    run = await loop.loops.run(loop.owner, agent_session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED
    steps = await loop.history(agent_session_id)
    changed = of_type(steps, AgentStepType.ENVIRONMENT_CHANGED)
    assert len(changed) == 1 and "by hand" in changed[0].as_text()
    told = text_of(loop.anthropic.calls[-1])
    assert "worked in it by hand" in told and "I fixed the total by hand." in told
