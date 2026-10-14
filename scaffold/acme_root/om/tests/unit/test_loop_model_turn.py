"""A turn of the model: its stream emits parts and never a step, a message
that lands mid-run reaches the next request, a known outage parks at
once, a reply cut by its bound is never sent again unchanged, a sink that
fails costs only the live view, and the wait before a provider is asked
again carries jitter."""

import asyncio
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from contracts.agent_step_storage import make_request, make_response
from contracts.loops import (
    ASSISTANT,
    loop_over,
    of_type,
    outage_parks_at_once_and_resumes_at_the_retry_time,
    person,
    ran_alone,
    reply,
    said,
    text_of,
    use,
)

from acme.integrations.model_providers.scripted import ScriptedFailure
from acme.integrations.model_providers.types import Effort, ErrorKind, StopReason
from acme.om.agent_sessions.limits import Limit, Limits, tally_loop, tripped
from acme.om.agent_sessions.types.agent_session import AgentSessionStatus
from acme.om.agent_steps.types.agent_step import Actor, AgentStep, AgentStepType
from acme.om.agent_steps.types.header import (
    LoopOutcome,
    ModelRequestHeader,
    ModelResponseHeader,
    ParkReason,
)
from acme.om.agent_steps.types.stream import StreamPart, TextPart, ToolInputPart
from acme.om.agents.impl.sink import StreamSinkMemoryImpl
from acme.om.agents.loop_rules import retry_wait
from acme.om.agents.types.run import RunEnd
from acme.om.base import new_id, utcnow
from acme.om.language_models.types.fill import MAIN, ModelAsk, ModelSize


def exchange(
    agent_session_id: UUID, loop_id: UUID, seq: int, *, answered: bool
) -> tuple[AgentStep, AgentStep]:
    """One unchanged main request and its response: whole when the provider
    answered it, abandoned when its call failed."""
    request = make_request(agent_session_id, loop_id, ()).model_copy(update={"seq": seq})
    header = (
        ModelResponseHeader(stop_reason=StopReason.END_TURN)
        if answered
        else ModelResponseHeader(abandoned=True)
    )
    response = make_response(agent_session_id, loop_id, request.id).model_copy(
        update={"seq": seq + 1, "header": header}
    )
    return request, response


def streak_of(steps: list[AgentStep], loop_id: UUID, limits: Limits) -> Limit | None:
    now = utcnow()
    trip = tripped(limits, tally_loop(steps, loop_id), now=now, run_started_at=now, deadline=None)
    return None if trip is None else trip.limit


class FailingSink(StreamSinkMemoryImpl):
    def emit(self, part: StreamPart) -> None:
        raise RuntimeError("the carrier went away")


async def test_the_stream_emits_numbered_parts_of_the_response_and_never_a_step(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "What is the total?")
    loop.anthropic.add(
        reply(said("Looking it up in the records now."), use("lookup", use_id="use_1")),
        reply(said("The total is 12.")),
    )

    await loop.loops.run(loop.owner, agent_session_id)

    steps = await loop.history(agent_session_id)
    responses = of_type(steps, AgentStepType.MODEL_RESPONSE)
    parts = loop.sink.parts
    first = [part for part in parts if part.agent_step_id == responses[0].id]
    assert [part.n for part in first] == list(range(len(first)))
    assert "".join(p.text for p in first if isinstance(p, TextPart)) == responses[0].as_text()
    assert any(isinstance(p, ToolInputPart) and p.tool_use_id == "use_1" for p in first)
    assert {part.agent_step_id for part in parts} <= {step.id for step in steps}
    assert len(steps) == 8, "a part adds up to a step, and is none"


async def test_a_broken_stream_ends_in_a_truncated_step_holding_what_arrived(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "What is the total?")
    partial = reply(said("The total is")).model_copy(
        update={"stop_reason": None, "truncated": True}
    )
    loop.anthropic.add(
        ScriptedFailure(kind=ErrorKind.TRANSIENT, partial=partial), reply(said("It is 12."))
    )

    run = await loop.loops.run(loop.owner, agent_session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED
    broken, whole = of_type(await loop.history(agent_session_id), AgentStepType.MODEL_RESPONSE)
    header = broken.header
    assert isinstance(header, ModelResponseHeader) and header.truncated
    assert broken.as_text() == "The total is"
    assert whole.as_text() == "It is 12."


async def test_a_message_that_lands_while_a_tool_runs_is_delivered_by_the_next_request(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "Find the total.")
    loop.anthropic.add(reply(use("slow", use_id="use_slow")), reply(said("Left the gains alone.")))
    slow = loop.tools["slow"]
    running = asyncio.ensure_future(loop.loops.run(loop.owner, agent_session_id))
    await slow.started.wait()
    steering = await loop.say(agent_session_id, "Do not change the gains.")
    slow.release.set()

    run = await running

    assert run.outcome is LoopOutcome.SUCCEEDED
    steps = await loop.history(agent_session_id)
    second = of_type(steps, AgentStepType.MODEL_REQUEST)[1]
    assert steering.id in second.refs, "the next request references it"
    assert "Do not change the gains." in text_of(loop.anthropic.calls[1])
    session = await loop.managers.agent_sessions.get_agent_session(loop.owner, agent_session_id)
    assert session.pending_input is None and session.status is AgentSessionStatus.IDLE


async def test_a_known_outage_parks_at_once_naming_the_provider_and_resumes_at_its_retry_time(
    tmp_path: Path,
) -> None:
    await outage_parks_at_once_and_resumes_at_the_retry_time(loop_over(tmp_path))


async def test_a_reply_cut_by_its_output_limit_is_never_sent_again_unchanged(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "Write the whole report.")
    cut = reply(said("The report begins")).model_copy(
        update={"stop_reason": StopReason.OUTPUT_LIMIT, "truncated": True}
    )
    loop.anthropic.add(cut, reply(said("A shorter report.")))

    run = await loop.loops.run(loop.owner, agent_session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED
    steps = await loop.history(agent_session_id)
    first, second = of_type(steps, AgentStepType.MODEL_REQUEST)
    assert isinstance(first.header, ModelRequestHeader)
    assert isinstance(second.header, ModelRequestHeader)
    assert first.header.prompt_hash != second.header.prompt_hash
    (told,) = [s for s in of_type(steps, AgentStepType.MESSAGE) if s.actor is Actor.ENGINE]
    assert "output limit" in told.as_text() and told.id in second.refs
    kept = of_type(steps, AgentStepType.MODEL_RESPONSE)[0].header
    assert isinstance(kept, ModelResponseHeader) and kept.truncated


def test_a_request_the_provider_answered_and_sent_again_unchanged_counts_toward_the_streak() -> (
    None
):
    agent_session_id, loop_id = new_id(), new_id()
    limits = Limits(error_streak=3)
    answered = [
        step
        for seq in range(1, 9, 2)
        for step in exchange(agent_session_id, loop_id, seq, answered=True)
    ]
    assert streak_of(answered[:6], loop_id, limits) is None
    assert streak_of(answered, loop_id, limits) is Limit.ERROR_STREAK, "the fourth, unchanged"
    retried = [
        step
        for seq in range(1, 21, 2)
        for step in exchange(agent_session_id, loop_id, seq, answered=False)
    ]
    assert streak_of(retried, loop_id, limits) is None, "a retry after a provider error is none"


async def test_a_message_that_lands_after_the_render_is_neither_delivered_nor_credited(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start()
    owner, colleague = loop.owner, loop.colleague()
    await loop.say(agent_session_id, "Find the total.")
    # The steer is the colleague's before their message lands, so it
    # instructs once a request delivers it.
    await loop.managers.agent_sessions.add_participant(owner, agent_session_id, colleague.user_id)
    await loop.managers.attribution.assign_principal(owner, agent_session_id, colleague.user_id)
    loop.anthropic.add(
        reply(use("lookup", use_id="use_1")),
        reply(use("lookup", q="what the colleague asked", use_id="use_2")),
        reply(said("Done.")),
    )
    hashes = loop.managers.windows._hashes  # type: ignore[attr-defined]
    keyed = hashes.keyed_hash
    landed: list[AgentStep] = []

    async def racing(ctx: object, sid: UUID, value: bytes) -> str:
        # After the render read the history, before its request is kept.
        if not landed:
            landed.append(await loop.say(agent_session_id, "Look up what I asked.", colleague))
        return await keyed(ctx, sid, value)

    hashes.keyed_hash = racing

    run = await loop.loops.run(owner, agent_session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED
    requests = of_type(await loop.history(agent_session_id), AgentStepType.MODEL_REQUEST)
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


async def test_a_provider_outage_that_outlasts_its_retries_parks_again_and_never_trips_the_streak(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "What is the total?")
    overloaded = ScriptedFailure(kind=ErrorKind.OVERLOADED, retry_after=2)
    loop.anthropic.add(*[overloaded] * 40)
    loop.openai.add(*[overloaded] * 40)
    ends: list[RunEnd] = []
    for _ in range(6):
        run = await loop.loops.run(loop.owner, agent_session_id)
        ends.append(run.end)
        assert run.park is not None and run.park.retry_at is not None, run
        assert run.park.reason is ParkReason.PROVIDER
        loop.clock.now = run.park.retry_at
        await loop.managers.agent_sessions.wake_agent_session(
            loop.owner, agent_session_id, run.park
        )

    assert ends == [RunEnd.PARKED] * 6, "it waits on the provider, and ends nothing"


async def test_a_sink_that_fails_costs_the_live_view_never_the_call(tmp_path: Path) -> None:
    loop = loop_over(tmp_path, sink=FailingSink())
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "What is the total?")
    loop.anthropic.add(
        reply(said("Looking."), use("lookup", use_id="use_1")), reply(said("The total is 12."))
    )

    run = await loop.loops.run(loop.owner, agent_session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED
    steps = await ran_alone(loop, agent_session_id)
    assert [s.as_text() for s in of_type(steps, AgentStepType.MODEL_RESPONSE)][-1] == (
        "The total is 12."
    )
    assert len(of_type(steps, AgentStepType.TOOL_RESPONSE)) == 1


def test_the_wait_before_asking_again_grows_carries_jitter_and_honors_the_retry_after() -> None:
    base = timedelta(seconds=1)
    assert retry_wait(None, 0, base, 0.0) == timedelta(seconds=0.5)
    assert retry_wait(None, 0, base, 0.99) < base
    assert retry_wait(None, 2, base, 0.0) == timedelta(seconds=2), "the fixed half doubles"
    assert retry_wait(None, 2, base, 0.0) < retry_wait(None, 2, base, 0.5)
    assert retry_wait(9.0, 1, base, 0.5) == timedelta(seconds=9)


async def test_the_loop_draws_its_retry_wait_from_its_jitter(tmp_path: Path) -> None:
    loop = loop_over(tmp_path, jitter=lambda: 0.5)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "What is the total?")
    loop.anthropic.add(ScriptedFailure(kind=ErrorKind.OVERLOADED), reply(said("The total is 12.")))
    before = loop.clock()

    run = await loop.loops.run(loop.owner, agent_session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED
    assert loop.clock() - before == timedelta(seconds=0.75), "half of 1s, and half of its half"


async def test_a_kind_that_asks_a_size_runs_its_turns_on_that_sizes_fill(tmp_path: Path) -> None:
    asked = ASSISTANT.model_copy(
        update={"asks": (ModelAsk(role=MAIN, size=ModelSize.XL, effort=Effort.LOW),)}
    )
    loop = loop_over(tmp_path, kinds=(asked,))
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "What is the total?")
    loop.anthropic.add(reply(said("The total is 12.")))

    run = await loop.loops.run(loop.owner, agent_session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED
    (call,) = loop.anthropic.calls
    assert (call.model, call.effort) == ("claude-opus-5-5", Effort.LOW)
    fills = await loop.managers.language_models.get_fill_set(loop.owner, agent_session_id)
    assert [r.ask for r in fills.roles] == [asked.asks[0], None], "the summarizer by its name"
