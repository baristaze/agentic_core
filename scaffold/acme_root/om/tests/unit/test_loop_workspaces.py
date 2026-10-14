"""A refused or lost workspace, over the memory storage and the scripted
provider (ADR 1009). A refusal that cannot clear, as each of the engine's
own providers gives, ends the loop `errored`, and its end report wakes a
parent waiting on it. One that waits for a workspace that may come parks
the loop on `resource`, to ask again after a wait, and a workspace whose
durable state is gone parks it for a person. No call is made. A park on
the workspace comes before its run settles anything, so it is marked
unsettled, and the run that resumes it settles each open call by its
effect; a run that resumed a settled park writes a settled one."""

from pathlib import Path
from uuid import UUID

import pytest
from contracts import sub_agents
from contracts.loops import call, loop_over, reply, said, use

from acme.infra.agent_workspaces import (
    AgentWorkspace,
    AgentWorkspaceLost,
    AgentWorkspaceProviderInterface,
    EgressMode,
    EgressPolicy,
    IsolationMode,
    IsolationRefused,
    IsolationSpec,
)
from acme.infra.agent_workspaces.twin import AgentWorkspaceNullImpl, AgentWorkspaceTwinImpl
from acme.om.agent_sessions.types.agent_session import AgentSessionStatus
from acme.om.agent_steps.types.agent_step import AgentStepType
from acme.om.agent_steps.types.header import (
    LoopEndedHeader,
    LoopOutcome,
    ParkedHeader,
    ParkReason,
    ToolRequestHeader,
)
from acme.om.agents.impl.loop import LoopOptions
from acme.om.agents.rules import CHILDREN_PARK
from acme.om.agents.types.run import RunEnd
from acme.om.context import TenantContext
from acme.om.tools.native.wait_for_sub_agents import WAIT_FOR_SUB_AGENTS

CONTAINER = IsolationSpec(mode=IsolationMode.CONTAINER, egress=EgressPolicy(mode=EgressMode.NONE))
"""A spec neither the twin nor the null provider makes."""


@pytest.mark.parametrize(
    "provider", [AgentWorkspaceNullImpl(), AgentWorkspaceTwinImpl()], ids=lambda p: p.describe()
)
async def test_a_refusal_that_cannot_clear_ends_the_loop_errored_and_wakes_its_waiting_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: AgentWorkspaceProviderInterface
) -> None:
    loop = loop_over(tmp_path, kinds=sub_agents.KINDS)
    root = await sub_agents.a_root(loop, "What is the quarterly total?")
    loop.anthropic.add(reply(sub_agents.spawn("the total")), reply(call(WAIT_FOR_SUB_AGENTS)))
    waiting = await loop.loops.run(loop.owner, root)
    assert waiting.park == CHILDREN_PARK
    (child,) = await sub_agents.children_of(loop, root)
    calls = len(loop.anthropic.calls)

    async def refused(
        ctx: TenantContext, agent_session_id: UUID, spec: IsolationSpec, **_: object
    ) -> AgentWorkspace:
        return await provider.prepare(ctx.org_id, agent_session_id, CONTAINER)

    monkeypatch.setattr(loop.managers.tools, "prepare_agent_workspace", refused)
    run = await loop.loops.run(loop.owner, child.id)

    assert (run.end, run.outcome, run.park) == (RunEnd.ENDED, LoopOutcome.ERRORED, None)
    with pytest.raises(IsolationRefused) as expected:
        await provider.prepare(loop.owner.org_id, child.id, CONTAINER)
    ended = (await loop.history(child.id))[-1]
    assert isinstance(ended.header, LoopEndedHeader) and expected.value.cause is not None
    assert ended.header.cause == expected.value.cause, "its end says what the spec asks"
    assert len(loop.anthropic.calls) == calls, "no call was made"
    woken = await loop.managers.agent_sessions.get_agent_session(loop.owner, root)
    assert (woken.status, woken.park) == (AgentSessionStatus.PENDING, None), "its report woke it"


async def test_a_refusal_that_clears_parks_on_the_resource_and_asks_again_after_the_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "What is the total?")
    prepare = loop.managers.tools.prepare_agent_workspace

    async def refused(*args: object, **kwargs: object) -> AgentWorkspace:
        raise IsolationRefused(
            "host-7 holds every workspace", clears=True, cause="no workspace is free yet"
        )

    monkeypatch.setattr(loop.managers.tools, "prepare_agent_workspace", refused)
    waiting = await loop.loops.run(loop.owner, agent_session_id)

    assert waiting.end is RunEnd.PARKED and waiting.park is not None, "never ended"
    assert (waiting.park.reason, waiting.park.unlock, waiting.park.retry_at) == (
        ParkReason.RESOURCE,
        "agent_workspace",
        loop.clock() + LoopOptions().agent_workspace_wait,
    )
    assert waiting.park.cause == "no workspace is free yet", "its cause, never its message"
    assert loop.anthropic.calls == [], "no call was made"

    monkeypatch.setattr(loop.managers.tools, "prepare_agent_workspace", prepare)
    assert waiting.park.retry_at is not None
    loop.clock.now = waiting.park.retry_at
    await loop.managers.agent_sessions.wake_agent_session(
        loop.owner, agent_session_id, waiting.park
    )
    loop.anthropic.add(reply(said("The total is 12.")))
    done = await loop.loops.run(loop.owner, agent_session_id)

    assert done.outcome is LoopOutcome.SUCCEEDED, "the workspace came, and the loop went on"


async def test_a_lost_workspace_parks_for_a_person_and_the_park_is_unsettled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "What is the total?")
    loop.anthropic.add(reply(said("Never asked.")))

    async def lost(*args: object, **kwargs: object) -> AgentWorkspace:
        raise AgentWorkspaceLost("what the workspace is rebuilt from is gone")

    monkeypatch.setattr(loop.managers.tools, "prepare_agent_workspace", lost)
    run = await loop.loops.run(loop.owner, agent_session_id)

    assert run.end is RunEnd.PARKED and run.park is not None, "never ended"
    assert (run.park.reason, run.park.unlock, run.park.retry_at) == (
        ParkReason.PERSON,
        "agent_workspace",
        None,
    )
    assert run.park.unsettled, "written before its run settled anything"
    parked = (await loop.history(agent_session_id))[-1]
    assert isinstance(parked.header, ParkedHeader) and parked.header.park.unsettled
    assert loop.anthropic.calls == [], "no call was made"


async def test_a_refused_isolation_after_a_settled_park_parks_on_the_resource_settled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "Find the total and send it.")
    loop.anthropic.add(reply(use("lookup", use_id="use_lookup")), reply(use("send")))
    asked = await loop.loops.run(loop.owner, agent_session_id)
    assert asked.park is not None and asked.park.unlock == "approval"
    assert not asked.park.unsettled, "the loop wrote it at a safe point"
    (send,) = [
        step
        for step in await loop.history(agent_session_id)
        if isinstance(step.header, ToolRequestHeader) and step.header.tool == "send"
    ]
    await loop.managers.tools.decide_call(loop.owner, agent_session_id, send.seq, approve=True)

    async def refused(*args: object, **kwargs: object) -> AgentWorkspace:
        raise IsolationRefused("no host can give it the workspace yet", clears=True)

    monkeypatch.setattr(loop.managers.tools, "prepare_agent_workspace", refused)
    waiting = await loop.loops.run(loop.owner, agent_session_id)

    assert waiting.end is RunEnd.PARKED and waiting.park is not None
    assert (waiting.park.reason, waiting.park.unlock) == (ParkReason.RESOURCE, "agent_workspace")
    assert waiting.park.retry_at is not None and waiting.park.retry_at > loop.clock()
    assert not waiting.park.unsettled, "its run resumed a settled park"
    assert loop.tools["send"].ran_as == [], "nothing ran"
    assert [s.type for s in await loop.history(agent_session_id)][-1] is AgentStepType.PARKED
