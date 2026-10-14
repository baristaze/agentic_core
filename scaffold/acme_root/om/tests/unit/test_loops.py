"""The loop over the memory storage and the scripted provider: a run
persists each step before it acts on it, starts no loop by itself on an
input that stopped one, nudges in a step, refuses a weaker workspace
before any call, reads a moved deadline again, reads only what each turn
added, and earns a call that keeps failing a notice."""

import asyncio
from datetime import timedelta
from itertools import pairwise
from pathlib import Path
from uuid import UUID

import pytest
from contracts.loops import (
    ASSISTANT,
    DELIVERY,
    loop_over,
    of_type,
    ran_alone,
    reply,
    said,
    text_of,
    use,
)

from acme.infra.agent_workspaces import (
    AgentWorkspace,
    AgentWorkspaceLost,
    EgressMode,
    EgressPolicy,
    IsolationMode,
    IsolationSpec,
)
from acme.integrations.model_providers.calls import ModelCall
from acme.integrations.model_providers.scripted import ScriptedFailure
from acme.integrations.model_providers.types import ErrorKind
from acme.om.agent_sessions.types.agent_session import AgentSessionStatus
from acme.om.agent_steps.types.agent_step import Actor, AgentStepType
from acme.om.agent_steps.types.content import ToolUseBlock
from acme.om.agent_steps.types.header import (
    LoopOutcome,
    ParkReason,
)
from acme.om.agent_steps.types.page import AgentStepPage
from acme.om.agents.impl.loop import LOST_SOURCE
from acme.om.agents.loop_rules import REPEATED
from acme.om.agents.types.run import RunEnd
from acme.om.context import TenantContext


def turns(call: ModelCall) -> list[str]:
    return [message.role for message in call.messages]


async def test_a_loop_runs_model_tool_model_and_succeeds_persisting_each_step_first(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start()
    trigger = await loop.say(agent_session_id, "What is the total?")
    asked = use("lookup", use_id="use_1")
    loop.anthropic.add(reply(said("Looking."), asked), reply(said("The total is 12.")))
    seen: list[list[AgentStepType]] = []
    tool = loop.tools["lookup"]
    original = tool.run

    async def watched(*args: object, **kwargs: object) -> object:
        # The tool runs only once its request is in the history.
        seen.append([step.type for step in await loop.history(agent_session_id)])
        return await original(*args, **kwargs)  # type: ignore[arg-type]

    tool.run = watched  # type: ignore[method-assign]

    run = await loop.loops.run(loop.owner, agent_session_id)

    assert (run.end, run.outcome) == (RunEnd.ENDED, LoopOutcome.SUCCEEDED)
    steps = await ran_alone(loop, agent_session_id)
    assert [step.type for step in steps] == [
        AgentStepType.MESSAGE,
        AgentStepType.MODEL_REQUEST,
        AgentStepType.MODEL_RESPONSE,
        AgentStepType.TOOL_REQUEST,
        AgentStepType.TOOL_RESPONSE,
        AgentStepType.MODEL_REQUEST,
        AgentStepType.MODEL_RESPONSE,
        AgentStepType.LOOP_ENDED,
    ]
    assert seen == [[step.type for step in steps[:4]]], "the request, before its tool ran"
    first, response, call, answer, _, _, ended = steps[1:]
    assert first.refs == (trigger.id,) and response.responds_to == first.id
    assert call.refs == (response.id,) and answer.responds_to == call.id
    assert all(step.loop_id == trigger.id for step in steps[1:])
    assert len(loop.anthropic.calls) == 2, "the response was kept before the second call"
    assert "lookup found the total" in text_of(loop.anthropic.calls[1])
    header = ended.header
    assert getattr(header, "outcome", None) is LoopOutcome.SUCCEEDED
    session = await loop.managers.agent_sessions.get_agent_session(loop.owner, agent_session_id)
    assert session.status is AgentSessionStatus.IDLE


async def test_an_errored_loop_starts_no_new_loop_on_the_input_it_left_undelivered(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start()
    first = await loop.say(agent_session_id, "What is the total?")
    loop.anthropic.add(ScriptedFailure(kind=ErrorKind.INVALID_REQUEST))

    run = await loop.loops.run(loop.owner, agent_session_id)

    assert run.outcome is LoopOutcome.ERRORED
    session = await loop.managers.agent_sessions.get_agent_session(loop.owner, agent_session_id)
    assert session.status is AgentSessionStatus.IDLE and session.pending_input is None
    again = await loop.loops.run(loop.owner, agent_session_id)
    assert again.end is RunEnd.IDLE and len(loop.anthropic.calls) == 1, "no run starts by itself"

    later = await loop.say(agent_session_id, "Try once more.")
    loop.anthropic.add(reply(said("The total is 12.")))
    retried = await loop.loops.run(loop.owner, agent_session_id)
    assert retried.outcome is LoopOutcome.SUCCEEDED
    request = of_type(await loop.history(agent_session_id), AgentStepType.MODEL_REQUEST)[-1]
    assert set(request.refs) == {first.id, later.id}, "the next request delivers both"


async def test_a_nudge_is_a_step_and_no_request_holds_two_model_turns_in_a_row(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start(DELIVERY.name)
    trigger = await loop.say(agent_session_id, "Deliver the report.")
    submit = ToolUseBlock(
        id="use_submit",
        name="submit",
        input={"claim": "succeeded", "evidence": [str(trigger.id)]},
    )
    loop.anthropic.add(reply(said("I will start.")), reply(said("Still thinking.")), reply(submit))

    run = await loop.loops.run(loop.owner, agent_session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED
    steps = await loop.history(agent_session_id)
    nudges = [step for step in of_type(steps, AgentStepType.MESSAGE) if step.actor is Actor.ENGINE]
    assert len(nudges) == 2 and all("submit" in nudge.as_text() for nudge in nudges)
    for call in loop.anthropic.calls:
        roles = turns(call)
        assert all(a != b for a, b in pairwise(roles)), roles
    delivered = {
        ref for request in of_type(steps, AgentStepType.MODEL_REQUEST) for ref in request.refs
    }
    assert {nudge.id for nudge in nudges} <= delivered, "each nudge was delivered"


async def test_a_workspace_that_cannot_meet_the_spec_ends_the_loop_before_any_call(
    tmp_path: Path,
) -> None:
    contained = DELIVERY.model_copy(
        update={
            "name": "contained",
            "isolation": IsolationSpec(
                mode=IsolationMode.CONTAINER, egress=EgressPolicy(mode=EgressMode.NONE)
            ),
        }
    )
    loop = loop_over(tmp_path, kinds=(contained,))
    agent_session_id = await loop.start("contained")
    await loop.say(agent_session_id, "Build it.")
    loop.anthropic.add(reply(said("Never asked.")))

    run = await loop.loops.run(loop.owner, agent_session_id)

    assert run.outcome is LoopOutcome.ERRORED
    assert loop.anthropic.calls == [] and loop.anthropic.remaining == 1, "no call was made"
    steps = await loop.history(agent_session_id)
    assert [step.type for step in steps] == [AgentStepType.MESSAGE, AgentStepType.LOOP_ENDED]


async def test_a_lost_workspace_parks_the_loop_for_a_person_before_any_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start("delivery")
    await loop.say(agent_session_id, "Build it.")
    loop.anthropic.add(reply(said("Never asked.")))

    async def lost(*args: object, **kwargs: object) -> AgentWorkspace:
        raise AgentWorkspaceLost("the branch of the session is gone, and nothing says why")

    monkeypatch.setattr(loop.managers.tools, "prepare_agent_workspace", lost)
    run = await loop.loops.run(loop.owner, agent_session_id)

    assert run.end is RunEnd.PARKED and run.park is not None, "never ended errored"
    assert (run.park.reason, run.park.unlock, run.park.retry_at) == (
        ParkReason.PERSON,
        "agent_workspace",
        None,
    ), "a person clears it, never a clock"
    assert run.park.cause == LOST_SOURCE, "the engine's words, never the provider's message"
    assert loop.anthropic.calls == [] and loop.anthropic.remaining == 1, "no call was made"


async def test_a_deadline_moved_while_a_run_works_is_read_again_before_it_parks(
    tmp_path: Path,
) -> None:
    """A run reads the tree's deadline when it begins. A person who moves it
    while a call runs moves it for that run too: past the old instant, the
    run reads it again and goes on, rather than parking on a deadline that
    no longer holds."""
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start()
    soon = loop.clock.now + timedelta(minutes=2)
    await loop.managers.agents.set_deadline(loop.owner, agent_session_id, soon)
    await loop.say(agent_session_id, "How long does the import take?")
    loop.anthropic.add(reply(use("slow", use_id="use_slow")), reply(said("About an hour.")))
    slow = loop.tools["slow"]
    running = asyncio.ensure_future(loop.loops.run(loop.owner, agent_session_id))
    await slow.started.wait()
    loop.clock.now += timedelta(minutes=5)
    later = loop.clock.now + timedelta(hours=1)
    await loop.managers.agents.set_deadline(loop.owner, agent_session_id, later)
    slow.release.set()
    run = await running

    assert (run.end, run.outcome) == (RunEnd.ENDED, LoopOutcome.SUCCEEDED)
    assert of_type(await loop.history(agent_session_id), AgentStepType.PARKED) == []


async def test_a_run_that_lost_its_claim_releases_nothing(tmp_path: Path) -> None:
    worked = ASSISTANT.model_copy(
        update={
            "name": "worked",
            "isolation": IsolationSpec(
                mode=IsolationMode.TWIN, egress=EgressPolicy(mode=EgressMode.NONE)
            ),
        }
    )
    loop = loop_over(tmp_path, kinds=(worked,))
    agent_session_id = await loop.start("worked")
    await loop.say(agent_session_id, "Find the total.")
    loop.anthropic.add(reply(use("slow", use_id="use_slow")))
    slow = loop.tools["slow"]
    running = asyncio.ensure_future(loop.loops.run(loop.owner, agent_session_id))
    await slow.started.wait()
    await loop.loops.take_over(loop.owner, agent_session_id)
    slow.release.set()

    fenced = await running

    assert fenced.end is RunEnd.STALE
    workspaces = loop.infra.get_agent_workspaces()
    assert agent_session_id in getattr(workspaces, "live", set()), "the person's workspace stays"


async def test_each_turn_reads_only_the_steps_added_since_the_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run reads its history whole once; each turn after reads only what
    was added since. So the rows four more tool turns read are the same
    over a session of 400 earlier steps as over one of none: what a turn
    reads does not grow with the history."""

    async def rows_read(earlier: int, turns: int) -> int:
        loop = loop_over(tmp_path / f"earlier-{earlier}-turns-{turns}")
        agent_session_id = await loop.start()
        for n in range(earlier):
            await loop.say(agent_session_id, f"note {n}")
        await loop.say(agent_session_id, "What is the total?")
        loop.anthropic.add(*(reply(use("lookup")) for _ in range(turns - 1)), reply(said("42")))
        read: list[int] = []
        get_agent_steps = loop.managers.agent_steps.get_agent_steps

        async def counted(
            ctx: TenantContext, agent_session_id: UUID, after_seq: int, limit: int
        ) -> AgentStepPage:
            page = await get_agent_steps(ctx, agent_session_id, after_seq, limit)
            read.append(len(page.items))
            return page

        monkeypatch.setattr(loop.managers.agent_steps, "get_agent_steps", counted)
        run = await loop.loops.run(loop.owner, agent_session_id)
        assert run.outcome is LoopOutcome.SUCCEEDED and len(loop.anthropic.calls) == turns
        return sum(read)

    short = await rows_read(0, 5) - await rows_read(0, 1)
    long = await rows_read(400, 5) - await rows_read(400, 1)
    assert long == short, f"four more turns read {long} rows over 400 steps, {short} over none"


async def test_a_call_that_keeps_failing_earns_a_notice_before_the_streak_ends_the_loop(
    tmp_path: Path,
) -> None:
    """The same failing call three times in a row: the model is told so
    before its next request, once, and the fifth ends the loop."""
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "What is the total?")
    loop.anthropic.add(*(reply(use("ledger")) for _ in range(5)))

    run = await loop.loops.run(loop.owner, agent_session_id)

    assert run.outcome is LoopOutcome.INCONCLUSIVE and len(loop.anthropic.calls) == 5
    steps = await loop.history(agent_session_id)
    notices = [s for s in of_type(steps, AgentStepType.MESSAGE) if s.actor is Actor.ENGINE]
    assert [n.as_text() for n in notices] == [REPEATED.format(tool="ledger", count=3)], (
        "one notice, at the third failure"
    )
    answers = of_type(steps, AgentStepType.TOOL_RESPONSE)
    requests = of_type(steps, AgentStepType.MODEL_REQUEST)
    assert answers[2].seq < notices[0].seq < requests[3].seq
    assert "failed 3 times in a row" in text_of(loop.anthropic.calls[3])
    assert "failed 3 times in a row" not in text_of(loop.anthropic.calls[2])
