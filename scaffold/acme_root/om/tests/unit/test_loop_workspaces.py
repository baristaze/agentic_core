"""A park on the workspace, over the memory storage and the scripted
provider (ADR 1009): a workspace no provider can meet the spec of parks the
loop on `resource`, to ask again after a wait, and one whose durable state
is gone parks it for a person; neither ends the loop, and no call is made.
Such a park comes before its run settles anything, so it is marked
unsettled, and the run that resumes it settles each open call by its
effect; a run that resumed a settled park writes a settled one."""

from pathlib import Path

import pytest
from contracts.loops import loop_over, reply, said, use

from acme.infra.workspaces import IsolationRefused, Workspace, WorkspaceLost
from acme.om.agents.types.run import RunEnd
from acme.om.steps.types.header import ParkedHeader, ParkReason, ToolRequestHeader
from acme.om.steps.types.step import StepType


async def test_a_lost_workspace_parks_for_a_person_and_the_park_is_unsettled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = loop_over(tmp_path)
    session_id = await loop.start()
    await loop.say(session_id, "What is the total?")
    loop.anthropic.add(reply(said("Never asked.")))

    async def lost(*args: object, **kwargs: object) -> Workspace:
        raise WorkspaceLost("what the workspace is rebuilt from is gone")

    monkeypatch.setattr(loop.managers.tools, "prepare_workspace", lost)
    run = await loop.loops.run(loop.owner, session_id)

    assert run.end is RunEnd.PARKED and run.park is not None, "never ended"
    assert (run.park.reason, run.park.unlock, run.park.retry_at) == (
        ParkReason.PERSON,
        "workspace",
        None,
    )
    assert run.park.unsettled, "written before its run settled anything"
    parked = (await loop.history(session_id))[-1]
    assert isinstance(parked.header, ParkedHeader) and parked.header.park.unsettled
    assert loop.anthropic.calls == [], "no call was made"


async def test_a_refused_isolation_after_a_settled_park_parks_on_the_resource_settled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = loop_over(tmp_path)
    session_id = await loop.start()
    await loop.say(session_id, "Find the total and send it.")
    loop.anthropic.add(reply(use("lookup", use_id="use_lookup")), reply(use("send")))
    asked = await loop.loops.run(loop.owner, session_id)
    assert asked.park is not None and asked.park.unlock == "approval"
    assert not asked.park.unsettled, "the loop wrote it at a safe point"
    (send,) = [
        step
        for step in await loop.history(session_id)
        if isinstance(step.header, ToolRequestHeader) and step.header.tool == "send"
    ]
    await loop.managers.tools.decide_call(loop.owner, session_id, send.seq, approve=True)

    async def refused(*args: object, **kwargs: object) -> Workspace:
        raise IsolationRefused("no host can give it the workspace yet")

    monkeypatch.setattr(loop.managers.tools, "prepare_workspace", refused)
    waiting = await loop.loops.run(loop.owner, session_id)

    assert waiting.end is RunEnd.PARKED and waiting.park is not None
    assert (waiting.park.reason, waiting.park.unlock) == (ParkReason.RESOURCE, "workspace")
    assert waiting.park.retry_at is not None and waiting.park.retry_at > loop.clock()
    assert not waiting.park.unsettled, "its run resumed a settled park"
    assert loop.tools["send"].ran_as == [], "nothing ran"
    assert [s.type for s in await loop.history(session_id)][-1] is StepType.PARKED
