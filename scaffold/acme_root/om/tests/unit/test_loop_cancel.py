"""A principal's cancel is a way out of every park a loop holds: a loop
parked on a person, on a job, or waiting in line ends `cancelled` on its
next run, with no model call. Every call it left open is answered, every
job it started is stopped, and it waits in no line."""

from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import UUID

import pytest
from contracts.loops import ASSISTANT, BUILDER, DELIVERY, Loop, call, loop_over, reply
from unit.test_loop_jobs import cancel, parked_on_a_job
from unit.test_loop_lines import held_resource, parked_in_line

from acme.om.agent_sessions.types.agent_session import SessionStatus
from acme.om.leases.types.request import WaiterKind
from acme.om.steps.types.header import LoopOutcome, ParkReason
from acme.om.steps.types.step import StepType
from acme.om.tools.types.policy import Decision, PolicyLayer, PolicyRule
from acme.om.tools.types.tool import ToolClass

APPROVES_FIRST = BUILDER.model_copy(
    update={
        "name": "approves_first",
        "policy": PolicyLayer(
            rules=(
                PolicyRule(authorization_class=ToolClass.WRITE, decision=Decision.ALLOW),
                PolicyRule(tool="build", decision=Decision.APPROVE),
            )
        ),
    }
)
"""A kind whose `build` job waits for a person's approval before it starts."""

Parked = Callable[[Path], Awaitable[tuple[Loop, UUID]]]


async def on_a_person(tmp_path: Path) -> tuple[Loop, UUID]:
    """A loop parked on a person, its job's call held for an approval."""
    loop = loop_over(tmp_path, kinds=(ASSISTANT, DELIVERY, APPROVES_FIRST))
    session_id = await loop.start("approves_first")
    await loop.say(session_id, "Start it.")
    loop.anthropic.add(reply(call("build", q="everything")))
    run = await loop.loops.run(loop.owner, session_id)
    assert run.park is not None and run.park.reason is ParkReason.PERSON, run
    return loop, session_id


async def on_a_job(tmp_path: Path) -> tuple[Loop, UUID]:
    """A loop parked on the job it started."""
    loop = loop_over(tmp_path, kinds=(ASSISTANT, DELIVERY, BUILDER))
    session_id, _ = await parked_on_a_job(loop)
    return loop, session_id


async def in_line(tmp_path: Path) -> tuple[Loop, UUID]:
    """A loop waiting in line for a resource another holds."""
    loop, _, _ = await held_resource(tmp_path)
    session_id, _, park = await parked_in_line(loop)
    assert park.reason is ParkReason.RESOURCE, park
    return loop, session_id


@pytest.mark.parametrize("parked", [on_a_person, on_a_job, in_line])
async def test_a_cancel_ends_a_parked_loop_stops_its_jobs_and_leaves_its_lines(
    tmp_path: Path, parked: Parked
) -> None:
    loop, session_id = await parked(tmp_path)
    called = len(loop.anthropic.calls)

    await loop.managers.agent_sessions.receive(loop.owner, session_id, [cancel(loop, session_id)])
    run = await loop.loops.run(loop.owner, session_id)

    assert run.outcome is LoopOutcome.CANCELLED
    assert len(loop.anthropic.calls) == called, "a cancel calls no model"
    session = await loop.managers.agent_sessions.get_session(loop.owner, session_id)
    assert (session.status, session.park) == (SessionStatus.IDLE, None)
    steps = await loop.history(session_id)
    answered = {step.responds_to for step in steps}
    requests = [step for step in steps if step.type is StepType.TOOL_REQUEST]
    assert all(request.id in answered for request in requests), "no call is left open"
    for job in loop.jobs.values():
        stopped = {handle.key for handle in job.cancelled}
        assert set(job.started) <= stopped, "every job it started is stopped"
    left = await loop.managers.leases.leave(loop.owner, WaiterKind.SESSION, session_id)
    assert left == 0, "it waits in no line"
