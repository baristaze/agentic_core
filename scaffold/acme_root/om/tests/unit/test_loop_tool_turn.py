"""The tool calls a response asked for: who a call runs as, a session that
holds private data asking before it acts outward, an interrupt that
stops the one call it names, an accepted result, and an input that is no
object reaching the model as invalid input."""

import asyncio
from pathlib import Path
from uuid import UUID

from contracts.loops import (
    ALLOWED,
    ASSISTANT,
    DELIVERY,
    Lookup,
    Loop,
    answer_to,
    control,
    loop_over,
    of_type,
    person,
    reply,
    said,
    text_of,
    use,
)

from acme.infra.agent_workspaces import (
    EgressMode,
    EgressPolicy,
    IsolationMode,
    IsolationSpec,
)
from acme.om.agent_steps.types.agent_step import AgentStep, AgentStepType
from acme.om.agent_steps.types.content import UNPARSED, ToolUseBlock
from acme.om.agent_steps.types.header import (
    ControlCommand,
    LoopOutcome,
    ModelRequestHeader,
    ParkReason,
    ToolFailure,
    ToolRequestHeader,
    ToolResponseHeader,
)
from acme.om.agents.types.request import Handoff, Spawn
from acme.om.agents.types.run import RunEnd
from acme.om.base import new_id
from acme.om.tools.rules import DEFAULT_CEILINGS
from acme.om.tools.types.policy import Decision, ToolPolicyLayer, ToolPolicyRule
from acme.om.tools.types.tool import ToolClass


def interrupt_of(loop: Loop, agent_session_id: UUID, request: AgentStep) -> AgentStep:
    return control(loop, agent_session_id, ControlCommand.INTERRUPT).model_copy(
        update={"refs": (request.id,)}
    )


async def test_a_second_person_who_writes_during_a_call_lends_it_nothing(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start()
    owner, colleague = loop.owner, loop.colleague()
    await loop.say(agent_session_id, "Find the total.")
    loop.anthropic.add(
        reply(use("slow", use_id="use_slow"), use("lookup", use_id="use_lookup")),
        reply(said("Done, and noted.")),
    )
    slow, lookup = loop.tools["slow"], loop.tools["lookup"]
    running = asyncio.ensure_future(loop.loops.run(owner, agent_session_id))
    await slow.started.wait()
    # The owner hands the steer on mid-call: only the steerer's message is
    # an instruction, and only it pays.
    await loop.managers.agent_sessions.add_participant(owner, agent_session_id, colleague.user_id)
    await loop.managers.attribution.assign_principal(owner, agent_session_id, colleague.user_id)
    await loop.say(agent_session_id, "Charge this one to me.", colleague)
    child = await loop.managers.agents.spawn(
        owner,
        agent_session_id,
        Spawn(id=new_id(), kind="assistant", title="a part", objective="Count."),
    )
    handed = await loop.managers.agents.hand_off(
        owner,
        agent_session_id,
        Handoff(id=new_id(), kind="assistant", title="the rest", objective="Report."),
    )
    slow.release.set()

    run = await running

    assert run.outcome is LoopOutcome.SUCCEEDED
    first, second = (
        step.header
        for step in of_type(await loop.history(agent_session_id), AgentStepType.MODEL_REQUEST)
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


async def test_a_marked_session_holding_private_data_asks_a_person_before_it_acts_outward(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "Find the total and send it.")
    loop.anthropic.add(
        reply(use("lookup", use_id="use_lookup")), reply(use("send", use_id="use_send"))
    )

    run = await loop.loops.run(loop.owner, agent_session_id)

    assert run.end is RunEnd.PARKED and run.park is not None
    assert (run.park.reason, run.park.unlock) == (ParkReason.PERSON, "approval")
    assert loop.tools["send"].ran_as == [], "it read data, so it waits before it sends"
    assert loop.tools["lookup"].ran_as == [loop.owner.user_id]


async def test_a_session_holding_no_private_data_acts_outward_unattended(tmp_path: Path) -> None:
    """The rule of two holds back only a session that holds private data.
    The platform's outward ceiling holds back every outward call, so the
    session acts unattended only where its adopter lifts that ceiling."""
    public = ASSISTANT.model_copy(update={"name": "public", "private_data": False})
    destructive_only = ToolPolicyLayer(
        rules=tuple(r for r in DEFAULT_CEILINGS.rules if r.authorization_class is not None)
    )
    ran: dict[bool, RunEnd] = {}
    for lifted in (False, True):
        loop = loop_over(
            tmp_path / str(lifted), kinds=(public,), ceilings=destructive_only if lifted else None
        )
        agent_session_id = await loop.start("public")
        await loop.say(agent_session_id, "Find the total and send it.")
        loop.anthropic.add(
            reply(use("lookup", use_id="use_lookup")),
            reply(use("send", use_id="use_send")),
            reply(said("Sent.")),
        )
        ran[lifted] = (await loop.loops.run(loop.owner, agent_session_id)).end
        assert loop.tools["send"].ran_as == ([loop.owner.user_id] if lifted else []), lifted

    assert ran == {False: RunEnd.PARKED, True: RunEnd.ENDED}


async def test_a_command_under_open_egress_runs_unattended_until_the_session_is_marked(
    tmp_path: Path,
) -> None:
    """A command in a workspace whose egress is open acts outward for the
    rule of two, and for nothing else: a session that holds private data
    but is not marked runs it as its kind's policy decides, and once a tool
    result marks the session, the same command waits for a person."""
    worker = ASSISTANT.model_copy(
        update={
            "name": "worker",
            "tools": ("lookup", "shell"),
            "isolation": IsolationSpec(
                mode=IsolationMode.TWIN, egress=EgressPolicy(mode=EgressMode.OPEN)
            ),
            "policy": ToolPolicyLayer(
                rules=(
                    *ALLOWED.rules,
                    ToolPolicyRule(authorization_class=ToolClass.EXECUTE, decision=Decision.ALLOW),
                )
            ),
        }
    )
    ran: dict[bool, RunEnd] = {}
    for marked in (False, True):
        shell = Lookup("shell", authorization_class=ToolClass.EXECUTE)
        loop = loop_over(tmp_path / str(marked), kinds=(worker,), extra=(shell,))
        agent_session_id = await loop.start("worker")
        await loop.say(agent_session_id, "Run the build.")
        first = (reply(use("lookup", use_id="use_lookup")),) if marked else ()
        loop.anthropic.add(*first, reply(use("shell", use_id="use_shell")), reply(said("Built.")))
        ran[marked] = (await loop.loops.run(loop.owner, agent_session_id)).end
        assert shell.ran_as == ([] if marked else [loop.owner.user_id]), marked

    assert ran == {False: RunEnd.ENDED, True: RunEnd.PARKED}


async def test_an_interrupt_stops_the_call_it_names_and_no_other(tmp_path: Path) -> None:
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "Find both.")
    loop.anthropic.add(
        reply(use("slow", use_id="use_slow"), use("lookup", use_id="use_lookup")),
        reply(said("One was stopped.")),
    )
    slow = loop.tools["slow"]
    running = asyncio.ensure_future(loop.loops.run(loop.owner, agent_session_id))
    await slow.started.wait()
    request = next(
        step
        for step in of_type(await loop.history(agent_session_id), AgentStepType.TOOL_REQUEST)
        if isinstance(step.header, ToolRequestHeader) and step.header.tool == "slow"
    )
    await loop.managers.agent_steps.append_inputs(
        loop.owner, agent_session_id, [interrupt_of(loop, agent_session_id, request)]
    )

    run = await running

    assert run.outcome is LoopOutcome.SUCCEEDED
    steps = await loop.history(agent_session_id)
    stopped = answer_to(steps, request).header
    assert isinstance(stopped, ToolResponseHeader) and stopped.failure is ToolFailure.INTERRUPTED
    assert loop.tools["lookup"].ran_as == [loop.owner.user_id]
    others = [a for a in of_type(steps, AgentStepType.TOOL_RESPONSE) if a.responds_to != request.id]
    assert [getattr(a.header, "failure", None) for a in others] == [None]


async def test_an_accepted_result_ends_the_loop_from_the_history_after_a_park(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start(DELIVERY.name)
    trigger = await loop.say(agent_session_id, "Deliver the report, and send it.")
    submit = ToolUseBlock(
        id="use_submit", name="submit", input={"claim": "succeeded", "evidence": [str(trigger.id)]}
    )
    loop.anthropic.add(reply(submit, use("send", use_id="use_send")))

    parked = await loop.loops.run(loop.owner, agent_session_id)

    assert parked.park is not None and parked.park.unlock == "approval"
    steps = await loop.history(agent_session_id)
    answered = next(
        s
        for s in of_type(steps, AgentStepType.TOOL_RESPONSE)
        if isinstance(s.header, ToolResponseHeader)
    )
    assert isinstance(answered.header, ToolResponseHeader)
    assert answered.header.accepted is not None, "the verdict is a step"
    send = next(
        s
        for s in of_type(steps, AgentStepType.TOOL_REQUEST)
        if isinstance(s.header, ToolRequestHeader) and s.header.tool == "send"
    )
    await loop.managers.tools.decide_call(loop.owner, agent_session_id, send.seq, approve=True)
    ended = await loop.loops.run(loop.owner, agent_session_id)

    assert ended.outcome is LoopOutcome.SUCCEEDED
    assert len(loop.anthropic.calls) == 1, "no model call after the accepted result"
    assert loop.tools["send"].ran_as == [loop.owner.user_id]


async def test_an_input_that_is_no_object_reaches_the_model_as_invalid_input(
    tmp_path: Path,
) -> None:
    """The model finished a call whose input is not one JSON object: the
    call never runs, its answer says why, and the loop goes on rather than
    ending on a turn that called nothing."""
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "What is the total?")
    broken = ToolUseBlock(id="use_1", name="lookup", input={UNPARSED: '{"q": "the total"'})
    loop.anthropic.add(reply(broken), reply(said("The total is 12.")))

    run = await loop.loops.run(loop.owner, agent_session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED and len(loop.anthropic.calls) == 2
    assert loop.tools["lookup"].ran_as == [], "the call never ran"
    (answer,) = of_type(await loop.history(agent_session_id), AgentStepType.TOOL_RESPONSE)
    header = answer.header
    assert isinstance(header, ToolResponseHeader) and header.failure is ToolFailure.INVALID_INPUT
    assert "not one JSON object" in text_of(loop.anthropic.calls[1])
