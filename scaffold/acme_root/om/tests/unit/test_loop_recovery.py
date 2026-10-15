"""What a lost run left: a new run refuses it, closes its open request with
its hold settled whole, and settles each call it may have started by its
effect, before a park, after a workspace park, under a cancel, and past
the tree's deadline."""

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

from acme.infra.agent_workspaces import (
    AgentWorkspace,
    AgentWorkspaceLost,
    IsolationRefused,
)
from acme.infra.exceptions import InfraException
from acme.om.agent_steps.types.agent_step import AgentStepType
from acme.om.agent_steps.types.header import (
    ControlCommand,
    LoopOutcome,
    ModelResponseHeader,
    ParkReason,
    ToolFailure,
    ToolRequestHeader,
    ToolResponseHeader,
)
from acme.om.agents.loop_rules import kind_prompts
from acme.om.agents.types.run import RunEnd
from acme.om.base import new_id
from acme.om.exceptions import StaleWriter
from acme.om.language_models.types.fill import MAIN, SUMMARIZER, Eligibility
from acme.om.tools.registry import ToolRegistry
from acme.om.windows.impl.gate import CallGateSpendBudgetImpl
from acme.om.windows.rules import request_step

from contracts.loops import (
    ASSISTANT,
    answer_to,
    control,
    loop_over,
    of_type,
    person,
    ran_alone,
    reply,
    said,
    use,
)


async def test_a_new_run_refuses_the_lost_one_and_settles_its_calls_by_their_effect(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "Note the fix.")
    loop.anthropic.add(
        reply(use("slow", use_id="use_slow"), use("note", use_id="use_note")),
        reply(said("Checked; the note did not finish.")),
    )
    slow, note = loop.tools["slow"], loop.tools["note"]
    lost = asyncio.ensure_future(loop.loops.run(loop.owner, agent_session_id))
    await slow.started.wait()

    # The lost run holds the slow call; a new run takes the session over.
    run = await loop.loops.run(loop.owner, agent_session_id)
    slow.release.set()
    stale = await lost

    assert (run.end, run.outcome) == (RunEnd.ENDED, LoopOutcome.SUCCEEDED)
    assert stale.end is RunEnd.STALE and stale.epoch < run.epoch
    steps = await ran_alone(loop, agent_session_id)
    answers = {step.responds_to: step for step in of_type(steps, AgentStepType.TOOL_RESPONSE)}
    requests = {
        step.header.tool: step
        for step in of_type(steps, AgentStepType.TOOL_REQUEST)
        if isinstance(step.header, ToolRequestHeader)
    }
    assert len(answers) == 2, "one answer each, the new run's"
    assert len(slow.ran_as) == 2, "the read-only call ran again under its key"
    assert note.ran_as == [], "the unsafe call never ran twice, nor once blind"
    noted_answer = answers[requests["note"].id].header
    assert (
        isinstance(noted_answer, ToolResponseHeader)
        and noted_answer.failure is ToolFailure.INTERRUPTED
    )
    with pytest.raises(StaleWriter):
        await loop.managers.agent_steps.append_agent_steps(
            loop.owner,
            agent_session_id,
            stale.epoch,
            [answers[requests["slow"].id].model_copy(update={"id": new_id()})],
        )


async def test_a_request_a_lost_run_left_open_is_closed_and_its_hold_settled_whole(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    managers, ctx = loop.managers, loop.owner
    agent_session_id = await loop.start()
    trigger = await loop.say(agent_session_id, "What is the total?")
    await managers.language_models.resolve_fill_set(
        ctx, agent_session_id, [MAIN, SUMMARIZER], Eligibility()
    )
    # The lost run: its epoch, its hold, its request, and nothing after.
    epoch = await managers.agent_steps.begin_run(ctx, agent_session_id)
    registry = ToolRegistry([loop.tools[name] for name in ASSISTANT.tools])
    prompts = kind_prompts(ASSISTANT, registry)
    rendered = await managers.windows.render_request(
        ctx, agent_session_id, epoch, trigger.id, prompts
    )
    fill = (await managers.language_models.get_fill_set(ctx, agent_session_id)).fill_for(MAIN)
    assert fill is not None
    gate = CallGateSpendBudgetImpl(
        managers.spend_budget_gate,
        managers.pricing,
        managers.agent_sessions,
        managers.spend_budgets,
    )
    hold = await gate.authorize(
        ctx, agent_session_id, person(ctx.user_id), MAIN, fill, rendered.call, credential="platform"
    )
    lost = request_step(
        rendered,
        rendered.attribution,
        agent_session_id,
        trigger.id,
        new_id(),
        loop.clock(),
        spend_hold_id=hold,
    )
    await managers.agent_steps.append_agent_steps(ctx, agent_session_id, epoch, [lost])
    loop.anthropic.add(reply(said("The total is 12.")))

    run = await loop.loops.run(ctx, agent_session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED
    steps = await loop.history(agent_session_id)
    closing = next(step for step in steps if step.responds_to == lost.id)
    assert isinstance(closing.header, ModelResponseHeader) and closing.header.abandoned
    ledger = loop.storage.get_ledger_storage()
    settlement = await ledger.read_spend_settlement(ctx.org_id, hold)
    assert settlement is not None and settlement.bill.kind == "unknown", "settled whole"
    # This run's gate never held the lost call: its record is named from the
    # request and the session, at the whole hold, and marked.
    record = next(
        r for r in await ledger.read_usage_records(ctx.org_id, agent_session_id, None, 10)
    )
    assert (record.spend_hold_id, record.agent_step_id, record.settled_whole) == (
        hold,
        closing.id,
        True,
    )
    assert (record.role, record.provider, record.model) == (MAIN, fill.provider.value, fill.model)
    assert (record.cost_micros, record.input_tokens) == (settlement.spent.cost_micros, 0)
    with pytest.raises(StaleWriter):
        await managers.agent_steps.append_agent_steps(
            ctx, agent_session_id, epoch, [closing.model_copy(update={"id": new_id()})]
        )


async def test_a_call_a_lost_run_may_have_started_is_settled_by_its_effect_before_a_park(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "Note the fix.")
    loop.anthropic.add(reply(use("note", use_id="use_note")), reply(said("Checked; done.")))
    note = loop.tools["note"]
    note.holds = True
    lost = asyncio.ensure_future(loop.loops.run(loop.owner, agent_session_id))
    await note.started.wait()
    await loop.managers.agent_steps.append_inputs(
        loop.owner, agent_session_id, [control(loop, agent_session_id, ControlCommand.PAUSE)]
    )

    paused = await loop.loops.run(loop.owner, agent_session_id)
    note.release.set()
    await lost
    await loop.managers.agent_steps.append_inputs(
        loop.owner, agent_session_id, [control(loop, agent_session_id, ControlCommand.RESUME)]
    )
    resumed = await loop.loops.run(loop.owner, agent_session_id)

    assert paused.park is not None and paused.park.reason is ParkReason.PAUSE
    assert resumed.outcome is LoopOutcome.SUCCEEDED
    assert len(note.ran_as) == 1, "the unsafe call never runs a second time"
    steps = await loop.history(agent_session_id)
    (request,) = of_type(steps, AgentStepType.TOOL_REQUEST)
    answer = answer_to(steps, request)
    assert getattr(answer.header, "failure", None) is ToolFailure.INTERRUPTED
    assert answer.seq < next(s.seq for s in steps if s.type is AgentStepType.PARKED), (
        "settled before the park"
    )


@pytest.mark.parametrize(
    ("refusal", "reason"),
    [
        (
            IsolationRefused("no host can give it the workspace yet", clears=True),
            ParkReason.LEASABLE_RESOURCE,
        ),
        (AgentWorkspaceLost("the branch of the session is gone"), ParkReason.PERSON),
    ],
)
async def test_a_call_a_lost_run_may_have_started_is_settled_by_its_effect_after_a_workspace_park(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    refusal: InfraException,
    reason: ParkReason,
) -> None:
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "Note the fix.")
    loop.anthropic.add(reply(use("note", use_id="use_note")), reply(said("Checked; done.")))
    note = loop.tools["note"]
    note.holds = True
    lost = asyncio.ensure_future(loop.loops.run(loop.owner, agent_session_id))
    await note.started.wait()
    prepare = loop.managers.tools.prepare_agent_workspace

    async def refused(*args: object, **kwargs: object) -> AgentWorkspace:
        raise refusal

    monkeypatch.setattr(loop.managers.tools, "prepare_agent_workspace", refused)
    parked = await loop.loops.run(loop.owner, agent_session_id)
    note.release.set()
    await lost
    monkeypatch.setattr(loop.managers.tools, "prepare_agent_workspace", prepare)
    await loop.managers.agent_steps.append_inputs(
        loop.owner, agent_session_id, [control(loop, agent_session_id, ControlCommand.UNLOCK)]
    )
    resumed = await loop.loops.run(loop.owner, agent_session_id)

    assert parked.park is not None and parked.park.reason is reason
    assert resumed.outcome is LoopOutcome.SUCCEEDED
    assert len(note.ran_as) == 1, "the unsafe call never runs a second time"
    steps = await loop.history(agent_session_id)
    (request,) = of_type(steps, AgentStepType.TOOL_REQUEST)
    assert getattr(answer_to(steps, request).header, "failure", None) is ToolFailure.INTERRUPTED


async def test_an_approved_call_held_back_by_a_park_runs_once_after_a_workspace_park(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "Find the total and send it.")
    loop.anthropic.add(
        reply(use("lookup", use_id="use_lookup")),
        reply(use("send", use_id="use_send")),
        reply(said("Sent.")),
    )
    asked = await loop.loops.run(loop.owner, agent_session_id)
    assert asked.park is not None and asked.park.unlock == "approval"
    send = next(
        s
        for s in of_type(await loop.history(agent_session_id), AgentStepType.TOOL_REQUEST)
        if isinstance(s.header, ToolRequestHeader) and s.header.tool == "send"
    )
    await loop.managers.tools.decide_call(loop.owner, agent_session_id, send.seq, approve=True)
    prepare = loop.managers.tools.prepare_agent_workspace

    async def refused(*args: object, **kwargs: object) -> AgentWorkspace:
        raise IsolationRefused("no host can give it the workspace yet", clears=True)

    monkeypatch.setattr(loop.managers.tools, "prepare_agent_workspace", refused)
    waiting = await loop.loops.run(loop.owner, agent_session_id)
    monkeypatch.setattr(loop.managers.tools, "prepare_agent_workspace", prepare)
    await loop.managers.agent_steps.append_inputs(
        loop.owner, agent_session_id, [control(loop, agent_session_id, ControlCommand.UNLOCK)]
    )
    sent = await loop.loops.run(loop.owner, agent_session_id)

    assert waiting.park is not None and waiting.park.reason is ParkReason.LEASABLE_RESOURCE
    assert sent.outcome is LoopOutcome.SUCCEEDED
    assert loop.tools["send"].ran_as == [loop.owner.user_id], "it never ran, so it runs"


async def test_a_cancel_answers_a_call_a_lost_run_may_have_started_as_unknown(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "Note the fix.")
    loop.anthropic.add(reply(use("note", use_id="use_note")))
    note = loop.tools["note"]
    note.holds = True
    lost = asyncio.ensure_future(loop.loops.run(loop.owner, agent_session_id))
    await note.started.wait()
    await loop.managers.agent_steps.append_inputs(
        loop.owner, agent_session_id, [control(loop, agent_session_id, ControlCommand.CANCEL)]
    )

    run = await loop.loops.run(loop.owner, agent_session_id)
    note.release.set()
    await lost

    assert run.outcome is LoopOutcome.CANCELLED
    answer = of_type(await loop.history(agent_session_id), AgentStepType.TOOL_RESPONSE)[-1]
    assert "unknown" in answer.as_tool_response().parts[0].text  # type: ignore[union-attr]
    assert "before it ran" not in answer.as_tool_response().parts[0].text  # type: ignore[union-attr]


async def test_a_call_a_lost_run_may_have_started_is_settled_past_the_trees_deadline(
    tmp_path: Path,
) -> None:
    """The tree's deadline bounds a fresh call's time, never the settling of
    one a lost run may have started: past it, that call is still answered
    by its effect, here unknown, and never as a timeout of its gate."""
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "Note the fix.")
    loop.anthropic.add(reply(use("note", use_id="use_note")))
    note = loop.tools["note"]
    note.holds, note.remote = True, True
    lost = asyncio.ensure_future(loop.loops.run(loop.owner, agent_session_id))
    await note.started.wait()
    passed = loop.clock.now - timedelta(minutes=1)
    await loop.managers.agents.set_deadline(loop.owner, agent_session_id, passed)

    run = await loop.loops.run(loop.owner, agent_session_id)
    note.release.set()
    await lost

    assert run.park is not None and run.park.unlock == "deadline"
    steps = await loop.history(agent_session_id)
    (request,) = of_type(steps, AgentStepType.TOOL_REQUEST)
    answer = answer_to(steps, request)
    assert getattr(answer.header, "failure", None) is ToolFailure.INTERRUPTED
    assert note.ran_as == [loop.owner.user_id], "never run a second time"
