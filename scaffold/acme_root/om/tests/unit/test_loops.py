"""The loop over the memory storage and the scripted provider: it persists
each step before it acts on it, settles a lost run's calls by their effect,
delivers a message that lands mid-run with the next request, parks at once
on a known outage, starts no loop by itself on an input that stopped one,
records who spoke and who pays for every request, nudges in a step, and
refuses a weaker workspace before any call. A call the model wrote that is
no object reaches it as invalid input, a sink that fails costs only the
live view, a call that keeps failing earns a notice, and the wait before a
provider is asked again carries jitter."""

import asyncio
from datetime import timedelta
from itertools import pairwise
from pathlib import Path
from uuid import UUID

import pytest
from contracts.loops import (
    ASSISTANT,
    DELIVERY,
    Loop,
    loop_over,
    outage_parks_at_once_and_resumes_at_the_retry_time,
    reply,
    said,
    use,
)
from contracts.step_storage import make_request, make_response

from acme.infra.workspaces import EgressMode, EgressPolicy, IsolationMode, IsolationSpec
from acme.integrations.model_providers.calls import ModelCall
from acme.integrations.model_providers.scripted import ScriptedFailure
from acme.integrations.model_providers.types import ErrorKind, StopReason
from acme.om.agent_sessions.limits import Limit, Limits, tally_loop, tripped
from acme.om.agent_sessions.types.agent_session import SessionStatus
from acme.om.agents.impl.sink import StreamSinkMemoryImpl
from acme.om.agents.loop_rules import REPEATED, kind_prompts, retry_wait
from acme.om.agents.types.request import Handoff, Spawn
from acme.om.agents.types.run import RunEnd
from acme.om.attribution.types.principal import Principal, PrincipalKind
from acme.om.base import new_id, utcnow
from acme.om.context import TenantContext
from acme.om.exceptions import StaleWriter
from acme.om.models.types.fill import MAIN, SUMMARIZER, Eligibility
from acme.om.steps.types.content import UNPARSED, TextBlock, ToolResultBlock, ToolUseBlock
from acme.om.steps.types.header import (
    ControlCommand,
    ControlHeader,
    LoopOutcome,
    ModelRequestHeader,
    ModelResponseHeader,
    ParkReason,
    ToolFailure,
    ToolRequestHeader,
    ToolResponseHeader,
)
from acme.om.steps.types.page import StepPage
from acme.om.steps.types.step import Actor, Origin, Step, StepType
from acme.om.steps.types.stream import StreamPart, TextPart, ToolInputPart
from acme.om.tools.registry import ToolRegistry
from acme.om.tools.rules import DEFAULT_CEILINGS
from acme.om.tools.types.policy import PolicyLayer
from acme.om.windows.impl.gate import CallGateBudgetImpl
from acme.om.windows.rules import request_step


def of_type(steps: list[Step], *types: StepType) -> list[Step]:
    return [step for step in steps if step.type in types]


def person(user_id: UUID) -> Principal:
    return Principal(kind=PrincipalKind.PERSON, id=user_id)


def turns(call: ModelCall) -> list[str]:
    return [message.role for message in call.messages]


def text_of(call: ModelCall) -> str:
    """Every text a call carries, its tool results' included."""
    texts: list[str] = []
    for message in call.messages:
        for block in message.blocks:
            parts = block.parts if isinstance(block, ToolResultBlock) else (block,)
            texts.extend(part.text for part in parts if isinstance(part, TextBlock))
    return "\n".join(texts)


async def ran_alone(loop: Loop, session_id: UUID) -> list[Step]:
    """The history once a run is done, after every step is checked to have
    a number of its own, in order."""
    steps = await loop.history(session_id)
    assert [step.seq for step in steps] == list(range(1, len(steps) + 1))
    return steps


# Check 1: model, tool, model, succeeded, each step persisted before its act.


async def test_a_loop_runs_model_tool_model_and_succeeds_persisting_each_step_first(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    session_id = await loop.start()
    trigger = await loop.say(session_id, "What is the total?")
    asked = use("lookup", use_id="use_1")
    loop.anthropic.add(reply(said("Looking."), asked), reply(said("The total is 12.")))
    seen: list[list[StepType]] = []
    tool = loop.tools["lookup"]
    original = tool.run

    async def watched(*args: object, **kwargs: object) -> object:
        # The tool runs only once its request is in the history.
        seen.append([step.type for step in await loop.history(session_id)])
        return await original(*args, **kwargs)  # type: ignore[arg-type]

    tool.run = watched  # type: ignore[method-assign]

    run = await loop.loops.run(loop.owner, session_id)

    assert (run.end, run.outcome) == (RunEnd.ENDED, LoopOutcome.SUCCEEDED)
    steps = await ran_alone(loop, session_id)
    assert [step.type for step in steps] == [
        StepType.MESSAGE,
        StepType.MODEL_REQUEST,
        StepType.MODEL_RESPONSE,
        StepType.TOOL_REQUEST,
        StepType.TOOL_RESPONSE,
        StepType.MODEL_REQUEST,
        StepType.MODEL_RESPONSE,
        StepType.LOOP_ENDED,
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
    session = await loop.managers.agent_sessions.get_session(loop.owner, session_id)
    assert session.status is SessionStatus.IDLE


async def test_the_stream_emits_numbered_parts_of_the_response_and_never_a_step(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    session_id = await loop.start()
    await loop.say(session_id, "What is the total?")
    loop.anthropic.add(
        reply(said("Looking it up in the records now."), use("lookup", use_id="use_1")),
        reply(said("The total is 12.")),
    )

    await loop.loops.run(loop.owner, session_id)

    steps = await loop.history(session_id)
    responses = of_type(steps, StepType.MODEL_RESPONSE)
    parts = loop.sink.parts
    first = [part for part in parts if part.step_id == responses[0].id]
    assert [part.n for part in first] == list(range(len(first)))
    assert "".join(p.text for p in first if isinstance(p, TextPart)) == responses[0].as_text()
    assert any(isinstance(p, ToolInputPart) and p.tool_use_id == "use_1" for p in first)
    assert {part.step_id for part in parts} <= {step.id for step in steps}
    assert len(steps) == 8, "a part adds up to a step, and is none"


async def test_a_broken_stream_ends_in_a_truncated_step_holding_what_arrived(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    session_id = await loop.start()
    await loop.say(session_id, "What is the total?")
    partial = reply(said("The total is")).model_copy(
        update={"stop_reason": None, "truncated": True}
    )
    loop.anthropic.add(
        ScriptedFailure(kind=ErrorKind.TRANSIENT, partial=partial), reply(said("It is 12."))
    )

    run = await loop.loops.run(loop.owner, session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED
    broken, whole = of_type(await loop.history(session_id), StepType.MODEL_RESPONSE)
    header = broken.header
    assert isinstance(header, ModelResponseHeader) and header.truncated
    assert broken.as_text() == "The total is"
    assert whole.as_text() == "It is 12."


# Check 2: a new epoch refuses the old run, and settles its calls by effect.


async def test_a_new_run_refuses_the_lost_one_and_settles_its_calls_by_their_effect(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    session_id = await loop.start()
    await loop.say(session_id, "Note the fix.")
    loop.anthropic.add(
        reply(use("slow", use_id="use_slow"), use("note", use_id="use_note")),
        reply(said("Checked; the note did not finish.")),
    )
    slow, note = loop.tools["slow"], loop.tools["note"]
    lost = asyncio.ensure_future(loop.loops.run(loop.owner, session_id))
    await slow.started.wait()

    # The lost run holds the slow call; a new run takes the session over.
    run = await loop.loops.run(loop.owner, session_id)
    slow.release.set()
    stale = await lost

    assert (run.end, run.outcome) == (RunEnd.ENDED, LoopOutcome.SUCCEEDED)
    assert stale.end is RunEnd.STALE and stale.epoch < run.epoch
    steps = await ran_alone(loop, session_id)
    answers = {step.responds_to: step for step in of_type(steps, StepType.TOOL_RESPONSE)}
    requests = {
        step.header.tool: step
        for step in of_type(steps, StepType.TOOL_REQUEST)
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
        await loop.managers.steps.append_steps(
            loop.owner,
            session_id,
            stale.epoch,
            [answers[requests["slow"].id].model_copy(update={"id": new_id()})],
        )


async def test_a_request_a_lost_run_left_open_is_closed_and_its_hold_settled_whole(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    managers, ctx = loop.managers, loop.owner
    session_id = await loop.start()
    trigger = await loop.say(session_id, "What is the total?")
    await managers.models.resolve_fill_set(ctx, session_id, [MAIN, SUMMARIZER], Eligibility())
    # The lost run: its epoch, its hold, its request, and nothing after.
    epoch = await managers.steps.begin_run(ctx, session_id)
    registry = ToolRegistry([loop.tools[name] for name in ASSISTANT.tools])
    prompts = kind_prompts(ASSISTANT, registry)
    rendered = await managers.windows.render_request(ctx, session_id, epoch, trigger.id, prompts)
    fill = (await managers.models.get_fill_set(ctx, session_id)).fill_for(MAIN)
    assert fill is not None
    gate = CallGateBudgetImpl(
        managers.budget_gate, managers.pricing, managers.agent_sessions, managers.budgets
    )
    hold = await gate.authorize(ctx, session_id, person(ctx.user_id), MAIN, fill, rendered.call)
    lost = request_step(
        rendered, rendered.attribution, session_id, trigger.id, new_id(), loop.clock(), hold_id=hold
    )
    await managers.steps.append_steps(ctx, session_id, epoch, [lost])
    loop.anthropic.add(reply(said("The total is 12.")))

    run = await loop.loops.run(ctx, session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED
    steps = await loop.history(session_id)
    closing = next(step for step in steps if step.responds_to == lost.id)
    assert isinstance(closing.header, ModelResponseHeader) and closing.header.abandoned
    ledger = loop.storage.get_ledger_storage()
    settlement = await ledger.read_settlement(ctx.org_id, hold)
    assert settlement is not None and settlement.bill.kind == "unknown", "settled whole"
    # This run's gate never held the lost call: its record is named from the
    # request and the session, at the whole hold, and marked.
    record = next(r for r in await ledger.read_usage_records(ctx.org_id, session_id, None, 10))
    assert (record.hold_id, record.step_id, record.settled_whole) == (hold, closing.id, True)
    assert (record.role, record.provider, record.model) == (MAIN, fill.provider.value, fill.model)
    assert (record.cost_micros, record.input_tokens) == (settlement.spent.cost_micros, 0)
    with pytest.raises(StaleWriter):
        await managers.steps.append_steps(
            ctx, session_id, epoch, [closing.model_copy(update={"id": new_id()})]
        )


# Check 3: a message that lands mid-run is delivered by the next request.


async def test_a_message_that_lands_while_a_tool_runs_is_delivered_by_the_next_request(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    session_id = await loop.start()
    await loop.say(session_id, "Find the total.")
    loop.anthropic.add(reply(use("slow", use_id="use_slow")), reply(said("Left the gains alone.")))
    slow = loop.tools["slow"]
    running = asyncio.ensure_future(loop.loops.run(loop.owner, session_id))
    await slow.started.wait()
    steering = await loop.say(session_id, "Do not change the gains.")
    slow.release.set()

    run = await running

    assert run.outcome is LoopOutcome.SUCCEEDED
    steps = await loop.history(session_id)
    second = of_type(steps, StepType.MODEL_REQUEST)[1]
    assert steering.id in second.refs, "the next request references it"
    assert "Do not change the gains." in text_of(loop.anthropic.calls[1])
    session = await loop.managers.agent_sessions.get_session(loop.owner, session_id)
    assert session.pending_input is None and session.status is SessionStatus.IDLE


# Check 4: a known outage parks a session at once, and it resumes at the retry time.


async def test_a_known_outage_parks_at_once_naming_the_provider_and_resumes_at_its_retry_time(
    tmp_path: Path,
) -> None:
    await outage_parks_at_once_and_resumes_at_the_retry_time(loop_over(tmp_path))


# Check 6: a loop that stopped wakes on nothing it already held.


async def test_an_errored_loop_starts_no_new_loop_on_the_input_it_left_undelivered(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    session_id = await loop.start()
    first = await loop.say(session_id, "What is the total?")
    loop.anthropic.add(ScriptedFailure(kind=ErrorKind.INVALID_REQUEST))

    run = await loop.loops.run(loop.owner, session_id)

    assert run.outcome is LoopOutcome.ERRORED
    session = await loop.managers.agent_sessions.get_session(loop.owner, session_id)
    assert session.status is SessionStatus.IDLE and session.pending_input is None
    again = await loop.loops.run(loop.owner, session_id)
    assert again.end is RunEnd.IDLE and len(loop.anthropic.calls) == 1, "no run starts by itself"

    later = await loop.say(session_id, "Try once more.")
    loop.anthropic.add(reply(said("The total is 12.")))
    retried = await loop.loops.run(loop.owner, session_id)
    assert retried.outcome is LoopOutcome.SUCCEEDED
    request = of_type(await loop.history(session_id), StepType.MODEL_REQUEST)[-1]
    assert set(request.refs) == {first.id, later.id}, "the next request delivers both"


async def test_a_cancelled_loop_starts_no_new_loop_on_a_message_it_left_undelivered(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    session_id = await loop.start()
    await loop.say(session_id, "Find the total.")
    loop.anthropic.add(reply(use("slow", use_id="use_slow")))
    slow = loop.tools["slow"]
    running = asyncio.ensure_future(loop.loops.run(loop.owner, session_id))
    await slow.started.wait()
    await loop.say(session_id, "Also the average.")
    cancel = Step(
        id=new_id(),
        created_at=loop.clock(),
        session_id=session_id,
        loop_id=new_id(),
        type=StepType.CONTROL,
        actor=Actor.PERSON,
        origin=Origin.PORTAL,
        header=ControlHeader(command=ControlCommand.CANCEL),
    )
    await loop.managers.steps.append_inputs(loop.owner, session_id, [cancel])

    run = await running

    assert run.outcome is LoopOutcome.CANCELLED
    answer = of_type(await loop.history(session_id), StepType.TOOL_RESPONSE)[-1]
    assert getattr(answer.header, "failure", None) is ToolFailure.INTERRUPTED
    session = await loop.managers.agent_sessions.get_session(loop.owner, session_id)
    assert session.status is SessionStatus.IDLE
    again = await loop.loops.run(loop.owner, session_id)
    assert again.end is RunEnd.IDLE and len(loop.anthropic.calls) == 1


# Check 8: every request records who spoke and who pays; a call, a spawn, and
# a hand-off run under the speaker of the request that produced them.


async def test_a_second_person_who_writes_during_a_call_lends_it_nothing(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    session_id = await loop.start()
    owner, colleague = loop.owner, loop.colleague()
    await loop.say(session_id, "Find the total.")
    loop.anthropic.add(
        reply(use("slow", use_id="use_slow"), use("lookup", use_id="use_lookup")),
        reply(said("Done, and noted.")),
    )
    slow, lookup = loop.tools["slow"], loop.tools["lookup"]
    running = asyncio.ensure_future(loop.loops.run(owner, session_id))
    await slow.started.wait()
    await loop.say(session_id, "Charge this one to me.", colleague)
    child = await loop.managers.agents.spawn(
        owner, session_id, Spawn(id=new_id(), kind="assistant", title="a part", objective="Count.")
    )
    handed = await loop.managers.agents.hand_off(
        owner,
        session_id,
        Handoff(id=new_id(), kind="assistant", title="the rest", objective="Report."),
    )
    slow.release.set()

    run = await running

    assert run.outcome is LoopOutcome.SUCCEEDED
    first, second = (
        step.header for step in of_type(await loop.history(session_id), StepType.MODEL_REQUEST)
    )
    assert isinstance(first, ModelRequestHeader) and isinstance(second, ModelRequestHeader)
    assert (first.speaker, first.spender) == (person(owner.user_id), person(owner.user_id))
    assert (second.speaker, second.spender) == (
        person(colleague.user_id),
        person(colleague.user_id),
    ), "the request that delivered the colleague's message"
    assert lookup.ran_as == [owner.user_id], "the call ran under the request that produced it"
    for made in (child, handed):
        authority = await loop.managers.attribution.get_authority(owner, made.id)
        assert authority.principal == person(owner.user_id)


# Check 9: a nudge is a step, and no request carries two of the model's turns
# in a row.


async def test_a_nudge_is_a_step_and_no_request_holds_two_model_turns_in_a_row(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    session_id = await loop.start(DELIVERY.name)
    trigger = await loop.say(session_id, "Deliver the report.")
    submit = ToolUseBlock(
        id="use_submit",
        name="submit",
        input={"claim": "succeeded", "evidence": [str(trigger.id)]},
    )
    loop.anthropic.add(reply(said("I will start.")), reply(said("Still thinking.")), reply(submit))

    run = await loop.loops.run(loop.owner, session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED
    steps = await loop.history(session_id)
    nudges = [step for step in of_type(steps, StepType.MESSAGE) if step.actor is Actor.ENGINE]
    assert len(nudges) == 2 and all("submit" in nudge.as_text() for nudge in nudges)
    for call in loop.anthropic.calls:
        roles = turns(call)
        assert all(a != b for a, b in pairwise(roles)), roles
    delivered = {ref for request in of_type(steps, StepType.MODEL_REQUEST) for ref in request.refs}
    assert {nudge.id for nudge in nudges} <= delivered, "each nudge was delivered"


# Check 10: a workspace that cannot meet the spec refuses before any call.


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
    session_id = await loop.start("contained")
    await loop.say(session_id, "Build it.")
    loop.anthropic.add(reply(said("Never asked.")))

    run = await loop.loops.run(loop.owner, session_id)

    assert run.outcome is LoopOutcome.ERRORED
    assert loop.anthropic.calls == [] and loop.anthropic.remaining == 1, "no call was made"
    steps = await loop.history(session_id)
    assert [step.type for step in steps] == [StepType.MESSAGE, StepType.LOOP_ENDED]


# Steering: controls out of band, and taking over.


def control(loop: Loop, session_id: UUID, command: ControlCommand) -> Step:
    return Step(
        id=new_id(),
        created_at=loop.clock(),
        session_id=session_id,
        loop_id=new_id(),
        type=StepType.CONTROL,
        actor=Actor.PERSON,
        origin=Origin.PORTAL,
        header=ControlHeader(command=command),
    )


async def test_a_pause_parks_at_the_next_safe_point_and_a_resume_takes_the_loop_up(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    session_id = await loop.start()
    await loop.say(session_id, "Find the total.")
    loop.anthropic.add(reply(use("slow", use_id="use_slow")), reply(said("It is 12.")))
    slow = loop.tools["slow"]
    running = asyncio.ensure_future(loop.loops.run(loop.owner, session_id))
    await slow.started.wait()
    await loop.managers.steps.append_inputs(
        loop.owner, session_id, [control(loop, session_id, ControlCommand.PAUSE)]
    )
    slow.release.set()

    paused = await running

    assert paused.end is RunEnd.PARKED and paused.park is not None
    assert paused.park.reason is ParkReason.PAUSE and len(loop.anthropic.calls) == 1
    await loop.managers.steps.append_inputs(
        loop.owner, session_id, [control(loop, session_id, ControlCommand.RESUME)]
    )
    resumed = await loop.loops.run(loop.owner, session_id)
    assert resumed.outcome is LoopOutcome.SUCCEEDED


async def test_taking_over_fences_the_run_and_giving_back_tells_the_model_what_changed(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    session_id = await loop.start()
    await loop.say(session_id, "Find the total.")
    loop.anthropic.add(reply(use("slow", use_id="use_slow")), reply(said("I see your change.")))
    slow = loop.tools["slow"]
    running = asyncio.ensure_future(loop.loops.run(loop.owner, session_id))
    await slow.started.wait()

    held = await loop.loops.take_over(loop.owner, session_id)
    slow.release.set()
    fenced = await running

    assert held.park is not None and held.park.reason is ParkReason.HANDOVER
    assert fenced.end is RunEnd.STALE, "the agent stands down"
    assert (await loop.loops.run(loop.owner, session_id)).end is RunEnd.IDLE
    given = await loop.loops.give_back(loop.owner, session_id, "I fixed the total by hand.")
    assert given.status is SessionStatus.PENDING
    run = await loop.loops.run(loop.owner, session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED
    steps = await loop.history(session_id)
    changed = of_type(steps, StepType.ENVIRONMENT_CHANGED)
    assert len(changed) == 1 and "by hand" in changed[0].as_text()
    told = text_of(loop.anthropic.calls[-1])
    assert "worked in it by hand" in told and "I fixed the total by hand." in told


# The rule of two: a marked session that holds private data asks a person
# before it acts outward.


async def test_a_marked_session_holding_private_data_asks_a_person_before_it_acts_outward(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    session_id = await loop.start()
    await loop.say(session_id, "Find the total and send it.")
    loop.anthropic.add(
        reply(use("lookup", use_id="use_lookup")), reply(use("send", use_id="use_send"))
    )

    run = await loop.loops.run(loop.owner, session_id)

    assert run.end is RunEnd.PARKED and run.park is not None
    assert (run.park.reason, run.park.unlock) == (ParkReason.PERSON, "approval")
    assert loop.tools["send"].ran_as == [], "it read data, so it waits before it sends"
    assert loop.tools["lookup"].ran_as == [loop.owner.user_id]


async def test_a_session_holding_no_private_data_acts_outward_unattended(tmp_path: Path) -> None:
    """The rule of two holds back only a session that holds private data.
    The platform's outward ceiling holds back every outward call, so the
    session acts unattended only where its adopter lifts that ceiling."""
    public = ASSISTANT.model_copy(update={"name": "public", "private_data": False})
    destructive_only = PolicyLayer(
        rules=tuple(r for r in DEFAULT_CEILINGS.rules if r.authorization_class is not None)
    )
    ran: dict[bool, RunEnd] = {}
    for lifted in (False, True):
        loop = loop_over(
            tmp_path / str(lifted), kinds=(public,), ceilings=destructive_only if lifted else None
        )
        session_id = await loop.start("public")
        await loop.say(session_id, "Find the total and send it.")
        loop.anthropic.add(
            reply(use("lookup", use_id="use_lookup")),
            reply(use("send", use_id="use_send")),
            reply(said("Sent.")),
        )
        ran[lifted] = (await loop.loops.run(loop.owner, session_id)).end
        assert loop.tools["send"].ran_as == ([loop.owner.user_id] if lifted else []), lifted

    assert ran == {False: RunEnd.PARKED, True: RunEnd.ENDED}


# Recovery before any park, a cut reply, attribution by delivery, a stale
# run's release, an interrupt's reach, and a verdict kept as a step.


def interrupt_of(loop: Loop, session_id: UUID, request: Step) -> Step:
    return control(loop, session_id, ControlCommand.INTERRUPT).model_copy(
        update={"refs": (request.id,)}
    )


def answer_to(steps: list[Step], request: Step) -> Step:
    return next(step for step in steps if step.responds_to == request.id)


async def test_a_call_a_lost_run_may_have_started_is_settled_by_its_effect_before_a_park(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    session_id = await loop.start()
    await loop.say(session_id, "Note the fix.")
    loop.anthropic.add(reply(use("note", use_id="use_note")), reply(said("Checked; done.")))
    note = loop.tools["note"]
    note.holds = True
    lost = asyncio.ensure_future(loop.loops.run(loop.owner, session_id))
    await note.started.wait()
    await loop.managers.steps.append_inputs(
        loop.owner, session_id, [control(loop, session_id, ControlCommand.PAUSE)]
    )

    paused = await loop.loops.run(loop.owner, session_id)
    note.release.set()
    await lost
    await loop.managers.steps.append_inputs(
        loop.owner, session_id, [control(loop, session_id, ControlCommand.RESUME)]
    )
    resumed = await loop.loops.run(loop.owner, session_id)

    assert paused.park is not None and paused.park.reason is ParkReason.PAUSE
    assert resumed.outcome is LoopOutcome.SUCCEEDED
    assert len(note.ran_as) == 1, "the unsafe call never runs a second time"
    steps = await loop.history(session_id)
    (request,) = of_type(steps, StepType.TOOL_REQUEST)
    answer = answer_to(steps, request)
    assert getattr(answer.header, "failure", None) is ToolFailure.INTERRUPTED
    assert answer.seq < next(s.seq for s in steps if s.type is StepType.PARKED), (
        "settled before the park"
    )


async def test_a_cancel_answers_a_call_a_lost_run_may_have_started_as_unknown(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    session_id = await loop.start()
    await loop.say(session_id, "Note the fix.")
    loop.anthropic.add(reply(use("note", use_id="use_note")))
    note = loop.tools["note"]
    note.holds = True
    lost = asyncio.ensure_future(loop.loops.run(loop.owner, session_id))
    await note.started.wait()
    await loop.managers.steps.append_inputs(
        loop.owner, session_id, [control(loop, session_id, ControlCommand.CANCEL)]
    )

    run = await loop.loops.run(loop.owner, session_id)
    note.release.set()
    await lost

    assert run.outcome is LoopOutcome.CANCELLED
    answer = of_type(await loop.history(session_id), StepType.TOOL_RESPONSE)[-1]
    assert "unknown" in answer.as_tool_response().parts[0].text  # type: ignore[union-attr]
    assert "before it ran" not in answer.as_tool_response().parts[0].text  # type: ignore[union-attr]


async def test_a_call_a_lost_run_may_have_started_is_settled_past_the_trees_deadline(
    tmp_path: Path,
) -> None:
    """The tree's deadline bounds a fresh call's time, never the settling of
    one a lost run may have started: past it, that call is still answered
    by its effect, here unknown, and never as a timeout of its gate."""
    loop = loop_over(tmp_path)
    session_id = await loop.start()
    await loop.say(session_id, "Note the fix.")
    loop.anthropic.add(reply(use("note", use_id="use_note")))
    note = loop.tools["note"]
    note.holds, note.remote = True, True
    lost = asyncio.ensure_future(loop.loops.run(loop.owner, session_id))
    await note.started.wait()
    passed = loop.clock.now - timedelta(minutes=1)
    await loop.managers.agents.set_deadline(loop.owner, session_id, passed)

    run = await loop.loops.run(loop.owner, session_id)
    note.release.set()
    await lost

    assert run.park is not None and run.park.unlock == "deadline"
    steps = await loop.history(session_id)
    (request,) = of_type(steps, StepType.TOOL_REQUEST)
    answer = answer_to(steps, request)
    assert getattr(answer.header, "failure", None) is ToolFailure.INTERRUPTED
    assert note.ran_as == [loop.owner.user_id], "never run a second time"


async def test_a_deadline_moved_while_a_run_works_is_read_again_before_it_parks(
    tmp_path: Path,
) -> None:
    """A run reads the tree's deadline when it begins. A person who moves it
    while a call runs moves it for that run too: past the old instant, the
    run reads it again and goes on, rather than parking on a deadline that
    no longer holds."""
    loop = loop_over(tmp_path)
    session_id = await loop.start()
    soon = loop.clock.now + timedelta(minutes=2)
    await loop.managers.agents.set_deadline(loop.owner, session_id, soon)
    await loop.say(session_id, "How long does the import take?")
    loop.anthropic.add(reply(use("slow", use_id="use_slow")), reply(said("About an hour.")))
    slow = loop.tools["slow"]
    running = asyncio.ensure_future(loop.loops.run(loop.owner, session_id))
    await slow.started.wait()
    loop.clock.now += timedelta(minutes=5)
    later = loop.clock.now + timedelta(hours=1)
    await loop.managers.agents.set_deadline(loop.owner, session_id, later)
    slow.release.set()
    run = await running

    assert (run.end, run.outcome) == (RunEnd.ENDED, LoopOutcome.SUCCEEDED)
    assert of_type(await loop.history(session_id), StepType.PARKED) == []


async def test_a_reply_cut_by_its_output_limit_is_never_sent_again_unchanged(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    session_id = await loop.start()
    await loop.say(session_id, "Write the whole report.")
    cut = reply(said("The report begins")).model_copy(
        update={"stop_reason": StopReason.OUTPUT_LIMIT, "truncated": True}
    )
    loop.anthropic.add(cut, reply(said("A shorter report.")))

    run = await loop.loops.run(loop.owner, session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED
    steps = await loop.history(session_id)
    first, second = of_type(steps, StepType.MODEL_REQUEST)
    assert isinstance(first.header, ModelRequestHeader)
    assert isinstance(second.header, ModelRequestHeader)
    assert first.header.prompt_hash != second.header.prompt_hash
    (told,) = [s for s in of_type(steps, StepType.MESSAGE) if s.actor is Actor.ENGINE]
    assert "output limit" in told.as_text() and told.id in second.refs
    kept = of_type(steps, StepType.MODEL_RESPONSE)[0].header
    assert isinstance(kept, ModelResponseHeader) and kept.truncated


def exchange(session_id: UUID, loop_id: UUID, seq: int, *, answered: bool) -> tuple[Step, Step]:
    """One unchanged main request and its response: whole when the provider
    answered it, abandoned when its call failed."""
    request = make_request(session_id, loop_id, ()).model_copy(update={"seq": seq})
    header = (
        ModelResponseHeader(stop_reason=StopReason.END_TURN)
        if answered
        else ModelResponseHeader(abandoned=True)
    )
    response = make_response(session_id, loop_id, request.id).model_copy(
        update={"seq": seq + 1, "header": header}
    )
    return request, response


def streak_of(steps: list[Step], loop_id: UUID, limits: Limits) -> Limit | None:
    now = utcnow()
    trip = tripped(limits, tally_loop(steps, loop_id), now=now, run_started_at=now, deadline=None)
    return None if trip is None else trip.limit


def test_a_request_the_provider_answered_and_sent_again_unchanged_counts_toward_the_streak() -> (
    None
):
    session_id, loop_id = new_id(), new_id()
    limits = Limits(error_streak=3)
    answered = [
        step for seq in range(1, 9, 2) for step in exchange(session_id, loop_id, seq, answered=True)
    ]
    assert streak_of(answered[:6], loop_id, limits) is None
    assert streak_of(answered, loop_id, limits) is Limit.ERROR_STREAK, "the fourth, unchanged"
    retried = [
        step
        for seq in range(1, 21, 2)
        for step in exchange(session_id, loop_id, seq, answered=False)
    ]
    assert streak_of(retried, loop_id, limits) is None, "a retry after a provider error is none"


async def test_a_message_that_lands_after_the_render_is_neither_delivered_nor_credited(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    session_id = await loop.start()
    owner, colleague = loop.owner, loop.colleague()
    await loop.say(session_id, "Find the total.")
    loop.anthropic.add(
        reply(use("lookup", use_id="use_1")),
        reply(use("lookup", q="what the colleague asked", use_id="use_2")),
        reply(said("Done.")),
    )
    hashes = loop.managers.windows._hashes  # type: ignore[attr-defined]
    keyed = hashes.keyed_hash
    landed: list[Step] = []

    async def racing(ctx: object, sid: UUID, value: bytes) -> str:
        # After the render read the history, before its request is kept.
        if not landed:
            landed.append(await loop.say(session_id, "Look up what I asked.", colleague))
        return await keyed(ctx, sid, value)

    hashes.keyed_hash = racing

    run = await loop.loops.run(owner, session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED
    requests = of_type(await loop.history(session_id), StepType.MODEL_REQUEST)
    for request in requests:
        header = request.header
        assert isinstance(header, ModelRequestHeader)
        who = colleague if landed[0].id in request.refs else owner
        assert header.speaker == person(who.user_id) or landed[0].id not in request.refs
    delivering = next(r for r in requests if landed[0].id in r.refs)
    assert isinstance(delivering.header, ModelRequestHeader)
    assert delivering.header.speaker == person(colleague.user_id)
    assert isinstance(requests[0].header, ModelRequestHeader)
    assert requests[0].header.speaker == person(owner.user_id), "it never read the message"
    assert loop.tools["lookup"].ran_as == [owner.user_id, colleague.user_id]


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
    session_id = await loop.start("worked")
    await loop.say(session_id, "Find the total.")
    loop.anthropic.add(reply(use("slow", use_id="use_slow")))
    slow = loop.tools["slow"]
    running = asyncio.ensure_future(loop.loops.run(loop.owner, session_id))
    await slow.started.wait()
    await loop.loops.take_over(loop.owner, session_id)
    slow.release.set()

    fenced = await running

    assert fenced.end is RunEnd.STALE
    workspaces = loop.infra.get_workspaces()
    assert session_id in getattr(workspaces, "live", set()), "the person's workspace stays"


async def test_an_interrupt_stops_the_call_it_names_and_no_other(tmp_path: Path) -> None:
    loop = loop_over(tmp_path)
    session_id = await loop.start()
    await loop.say(session_id, "Find both.")
    loop.anthropic.add(
        reply(use("slow", use_id="use_slow"), use("lookup", use_id="use_lookup")),
        reply(said("One was stopped.")),
    )
    slow = loop.tools["slow"]
    running = asyncio.ensure_future(loop.loops.run(loop.owner, session_id))
    await slow.started.wait()
    request = next(
        step
        for step in of_type(await loop.history(session_id), StepType.TOOL_REQUEST)
        if isinstance(step.header, ToolRequestHeader) and step.header.tool == "slow"
    )
    await loop.managers.steps.append_inputs(
        loop.owner, session_id, [interrupt_of(loop, session_id, request)]
    )

    run = await running

    assert run.outcome is LoopOutcome.SUCCEEDED
    steps = await loop.history(session_id)
    stopped = answer_to(steps, request).header
    assert isinstance(stopped, ToolResponseHeader) and stopped.failure is ToolFailure.INTERRUPTED
    assert loop.tools["lookup"].ran_as == [loop.owner.user_id]
    others = [a for a in of_type(steps, StepType.TOOL_RESPONSE) if a.responds_to != request.id]
    assert [getattr(a.header, "failure", None) for a in others] == [None]


async def test_an_accepted_result_ends_the_loop_from_the_history_after_a_park(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    session_id = await loop.start(DELIVERY.name)
    trigger = await loop.say(session_id, "Deliver the report, and send it.")
    submit = ToolUseBlock(
        id="use_submit", name="submit", input={"claim": "succeeded", "evidence": [str(trigger.id)]}
    )
    loop.anthropic.add(reply(submit, use("send", use_id="use_send")))

    parked = await loop.loops.run(loop.owner, session_id)

    assert parked.park is not None and parked.park.unlock == "approval"
    steps = await loop.history(session_id)
    answered = next(
        s
        for s in of_type(steps, StepType.TOOL_RESPONSE)
        if isinstance(s.header, ToolResponseHeader)
    )
    assert isinstance(answered.header, ToolResponseHeader)
    assert answered.header.accepted is not None, "the verdict is a step"
    send = next(
        s
        for s in of_type(steps, StepType.TOOL_REQUEST)
        if isinstance(s.header, ToolRequestHeader) and s.header.tool == "send"
    )
    await loop.managers.tools.decide_call(loop.owner, session_id, send.seq, approve=True)
    ended = await loop.loops.run(loop.owner, session_id)

    assert ended.outcome is LoopOutcome.SUCCEEDED
    assert len(loop.anthropic.calls) == 1, "no model call after the accepted result"
    assert loop.tools["send"].ran_as == [loop.owner.user_id]


async def test_a_provider_outage_that_outlasts_its_retries_parks_again_and_never_trips_the_streak(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    session_id = await loop.start()
    await loop.say(session_id, "What is the total?")
    overloaded = ScriptedFailure(kind=ErrorKind.OVERLOADED, retry_after=2)
    loop.anthropic.add(*[overloaded] * 40)
    loop.openai.add(*[overloaded] * 40)
    ends: list[RunEnd] = []
    for _ in range(6):
        run = await loop.loops.run(loop.owner, session_id)
        ends.append(run.end)
        assert run.park is not None and run.park.retry_at is not None, run
        assert run.park.reason is ParkReason.PROVIDER
        loop.clock.now = run.park.retry_at
        await loop.managers.agent_sessions.wake_session(loop.owner, session_id, run.park)

    assert ends == [RunEnd.PARKED] * 6, "it waits on the provider, and ends nothing"


# What a model wrote that is no object, a sink that fails, a call that keeps
# failing, and the wait before a provider is asked again.


async def test_an_input_that_is_no_object_reaches_the_model_as_invalid_input(
    tmp_path: Path,
) -> None:
    """The model finished a call whose input is not one JSON object: the
    call never runs, its answer says why, and the loop goes on rather than
    ending on a turn that called nothing."""
    loop = loop_over(tmp_path)
    session_id = await loop.start()
    await loop.say(session_id, "What is the total?")
    broken = ToolUseBlock(id="use_1", name="lookup", input={UNPARSED: '{"q": "the total"'})
    loop.anthropic.add(reply(broken), reply(said("The total is 12.")))

    run = await loop.loops.run(loop.owner, session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED and len(loop.anthropic.calls) == 2
    assert loop.tools["lookup"].ran_as == [], "the call never ran"
    (answer,) = of_type(await loop.history(session_id), StepType.TOOL_RESPONSE)
    header = answer.header
    assert isinstance(header, ToolResponseHeader) and header.failure is ToolFailure.INVALID_INPUT
    assert "not one JSON object" in text_of(loop.anthropic.calls[1])


class FailingSink(StreamSinkMemoryImpl):
    def emit(self, part: StreamPart) -> None:
        raise RuntimeError("the carrier went away")


async def test_a_sink_that_fails_costs_the_live_view_never_the_call(tmp_path: Path) -> None:
    loop = loop_over(tmp_path, sink=FailingSink())
    session_id = await loop.start()
    await loop.say(session_id, "What is the total?")
    loop.anthropic.add(
        reply(said("Looking."), use("lookup", use_id="use_1")), reply(said("The total is 12."))
    )

    run = await loop.loops.run(loop.owner, session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED
    steps = await ran_alone(loop, session_id)
    assert [s.as_text() for s in of_type(steps, StepType.MODEL_RESPONSE)][-1] == (
        "The total is 12."
    )
    assert len(of_type(steps, StepType.TOOL_RESPONSE)) == 1


async def test_each_turn_reads_only_the_steps_added_since_the_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run reads its history whole once; each turn after reads only what
    was added since. So the rows four more tool turns read are the same
    over a session of 400 earlier steps as over one of none: what a turn
    reads does not grow with the history."""

    async def rows_read(earlier: int, turns: int) -> int:
        loop = loop_over(tmp_path / f"earlier-{earlier}-turns-{turns}")
        session_id = await loop.start()
        for n in range(earlier):
            await loop.say(session_id, f"note {n}")
        await loop.say(session_id, "What is the total?")
        loop.anthropic.add(*(reply(use("lookup")) for _ in range(turns - 1)), reply(said("42")))
        read: list[int] = []
        get_steps = loop.managers.steps.get_steps

        async def counted(
            ctx: TenantContext, session_id: UUID, after_seq: int, limit: int
        ) -> StepPage:
            page = await get_steps(ctx, session_id, after_seq, limit)
            read.append(len(page.items))
            return page

        monkeypatch.setattr(loop.managers.steps, "get_steps", counted)
        run = await loop.loops.run(loop.owner, session_id)
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
    session_id = await loop.start()
    await loop.say(session_id, "What is the total?")
    loop.anthropic.add(*(reply(use("ledger")) for _ in range(5)))

    run = await loop.loops.run(loop.owner, session_id)

    assert run.outcome is LoopOutcome.INCONCLUSIVE and len(loop.anthropic.calls) == 5
    steps = await loop.history(session_id)
    notices = [s for s in of_type(steps, StepType.MESSAGE) if s.actor is Actor.ENGINE]
    assert [n.as_text() for n in notices] == [REPEATED.format(tool="ledger", count=3)], (
        "one notice, at the third failure"
    )
    answers = of_type(steps, StepType.TOOL_RESPONSE)
    requests = of_type(steps, StepType.MODEL_REQUEST)
    assert answers[2].seq < notices[0].seq < requests[3].seq
    assert "failed 3 times in a row" in text_of(loop.anthropic.calls[3])
    assert "failed 3 times in a row" not in text_of(loop.anthropic.calls[2])


def test_the_wait_before_asking_again_grows_carries_jitter_and_honors_the_retry_after() -> None:
    base = timedelta(seconds=1)
    assert retry_wait(None, 0, base, 0.0) == timedelta(seconds=0.5)
    assert retry_wait(None, 0, base, 0.99) < base
    assert retry_wait(None, 2, base, 0.0) == timedelta(seconds=2), "the fixed half doubles"
    assert retry_wait(None, 2, base, 0.0) < retry_wait(None, 2, base, 0.5)
    assert retry_wait(9.0, 1, base, 0.5) == timedelta(seconds=9)


async def test_the_loop_draws_its_retry_wait_from_its_jitter(tmp_path: Path) -> None:
    loop = loop_over(tmp_path, jitter=lambda: 0.5)
    session_id = await loop.start()
    await loop.say(session_id, "What is the total?")
    loop.anthropic.add(ScriptedFailure(kind=ErrorKind.OVERLOADED), reply(said("The total is 12.")))
    before = loop.clock()

    run = await loop.loops.run(loop.owner, session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED
    assert loop.clock() - before == timedelta(seconds=0.75), "half of 1s, and half of its half"
