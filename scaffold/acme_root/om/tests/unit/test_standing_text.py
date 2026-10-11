"""A session's standing text: the instructions a principal pins on it. It
is pinned when the session is made, renders whole in the system layer of
every request, before and after a compaction, with a prefix that stays
byte for byte the same, changes only at a recorded switch and from the
next loop on, and is refused past its bound when it is pinned."""

import json
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

import pytest
from contracts.agent_session_storage import make_session
from contracts.histories import History
from contracts.loops import ASSISTANT, SONNET, Loop, loop_over, reply, said, use
from pydantic import BaseModel, ValidationError

from acme.integrations.model_providers.anthropic import request_body
from acme.integrations.model_providers.calls import ModelCall, ModelReply
from acme.om.agent_sessions.types.agent_session import SessionStatus
from acme.om.agents.loop_rules import kind_prompts, standing_text, start_seq
from acme.om.agents.types.request import MAX_STANDING, Handoff, Spawn, Start, SwitchStanding
from acme.om.agents.types.run import RunEnd
from acme.om.base import new_id
from acme.om.exceptions import NotAuthorized
from acme.om.models.types.fill import MAIN
from acme.om.steps.types.content import Content, TextBlock
from acme.om.steps.types.header import (
    ControlCommand,
    LoopOutcome,
    ModelRequestHeader,
    StandingSwitch,
    SwitchedHeader,
)
from acme.om.steps.types.step import Actor, Origin, Step, StepType, shape_refusal
from acme.om.tools.native.load_toolset import loads
from acme.om.tools.registry import ToolRegistry
from acme.om.windows.rules import DATA_NOTICE, STANDING_LEAD, render_main
from acme.om.windows.types.policy import CompactionPolicy

MANUAL = "\n".join(f"{n:03d}. Quote the ledger's line for each total you give." for n in range(200))
"""An organization's manual: past the pinned zone's bound, which would cut
it to an excerpt, and within its own."""

REVISED = MANUAL.replace("Quote the ledger's line", "Cite the ledger's page")


def main_calls(loop: Loop) -> list[ModelCall]:
    return [call for call in loop.anthropic.calls if call.model == SONNET]


def prefix(call: ModelCall) -> bytes:
    """The bytes the provider caches before the conversation: the tool
    definitions and the system layer, as the adapter sends them."""
    body = request_body(call)[0]
    return json.dumps([body.get("tools"), body.get("system")]).encode()


def cached(call: ModelCall) -> list[tuple[str, bytes]]:
    """Each breakpoint in the system layer, as the adapter sends it: the
    text of the block it sits on, and the bytes the provider caches there,
    the tool definitions and the system layer up to that block."""
    body, dropped = request_body(call)
    assert not dropped, "every marker within the provider's limit"
    system = body["system"]
    return [
        (block["text"], json.dumps([body.get("tools"), system[: index + 1]]).encode())
        for index, block in enumerate(system)
        if "cache_control" in block
    ]


def texts(call: ModelCall) -> list[str]:
    """Every text the call's turns carry."""
    return [b.text for m in call.messages for b in m.blocks if isinstance(b, TextBlock)]


async def started(loop: Loop, standing: str) -> UUID:
    start = Start(id=new_id(), kind="assistant", title="the ledger", standing=standing)
    return (await loop.managers.agents.start_session(loop.owner, start)).id


async def asked(loop: Loop, session_id: UUID, question: str, *replies: ModelReply) -> None:
    await loop.say(session_id, question)
    loop.anthropic.add(*replies)
    run = await loop.loops.run(loop.owner, session_id)
    assert (run.end, run.outcome) == (RunEnd.ENDED, LoopOutcome.SUCCEEDED)


async def replayed(loop: Loop, session_id: UUID) -> list[ModelCall]:
    """Each main request rendered again from the steps before it, its
    standing text read off them as the run of its loop read it."""
    steps = await loop.history(session_id)
    session = await loop.managers.agent_sessions.get_session(loop.owner, session_id)
    fill = (await loop.managers.models.get_fill_set(loop.owner, session_id)).fill_for(MAIN)
    assert fill is not None
    registry = ToolRegistry(tool for tool in loop.tools.values() if tool.spec.name in session.tools)
    calls: list[ModelCall] = []
    for index, step in enumerate(steps):
        header = step.header
        if not isinstance(header, ModelRequestHeader) or header.role != MAIN:
            continue
        before = steps[:index]
        standing = standing_text(before, start_seq(before, step.loop_id))
        prompts = kind_prompts(ASSISTANT, registry, loads(before), standing)
        drafted = render_main(
            before, prompts, fill, header.fill_set_version, None, CompactionPolicy()
        )
        calls.append(drafted.call)
    return calls


# Check 1: whole in the system layer of every request, with one prefix.


async def test_standing_text_renders_whole_in_the_system_layer_through_a_compaction(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    session_id = await started(loop, MANUAL)
    await asked(loop, session_id, "What is the total?", reply(use("lookup")), reply(said("12.")))
    compact = History(session_id).control(ControlCommand.COMPACT)
    await loop.managers.steps.append_inputs(loop.owner, session_id, [compact])
    await asked(
        loop,
        session_id,
        "And the tax?",
        reply(said("Asked for the total; it is 12."), model="claude-haiku-4-5"),
        reply(use("lookup")),
        reply(said("1.2.")),
    )
    await asked(loop, session_id, "Thanks.", reply(said("Glad to help.")))

    steps = await loop.history(session_id)
    (summary,) = [step for step in steps if step.type is StepType.SUMMARY]
    (pinned,) = [step for step in steps if step.type is StepType.SWITCHED]
    first = min(step.seq for step in steps if step.type is StepType.MESSAGE)
    assert pinned.seq < first, "pinned before anything arrives"
    after = [
        step
        for step in steps
        if isinstance(step.header, ModelRequestHeader)
        and step.header.role == MAIN
        and step.header.summary_id == summary.id
    ]
    calls = main_calls(loop)
    assert (len(calls), len(after)) == (5, 3), "two requests before the compaction, three after"
    for call in calls:
        assert [block.text for block in call.system] == [
            ASSISTANT.prompts[0],
            f"{STANDING_LEAD}\n\n{MANUAL}",
            DATA_NOTICE,
        ], "after the kind's prompts, whole, never an excerpt"
        assert not any(MANUAL[:60] in text for text in texts(call)), "never in a turn"
    assert len({prefix(call) for call in calls}) == 1, "one cached prefix, byte for byte"
    (summarizer,) = [call for call in loop.anthropic.calls if call.model != SONNET]
    folded = [block.text for block in summarizer.system] + texts(summarizer)
    assert not any(MANUAL[:60] in text for text in folded), "no summary folds it in as data"


# Sessions of a kind version share the cached tools and prompts, whatever
# standing text each pins.


async def test_sessions_of_a_kind_share_the_cached_tools_and_prompts_whatever_each_pins(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    manual = await started(loop, MANUAL)
    await asked(loop, manual, "What is the total?", reply(use("lookup")), reply(said("12.")))
    for standing in (REVISED, ""):
        await asked(loop, await started(loop, standing), "What is the total?", reply(said("12.")))
    calls = main_calls(loop)
    assert len(calls) == 4, "two requests of the manual's session, one of each other"
    assert len([m for m in calls[1].messages if m.cache]) == 2, "both turn markers in use"
    breakpoints = [cached(call) for call in calls]
    assert all(len(points) == 2 for points in breakpoints), "two in the system layer"
    ((text, _),) = {kind for kind, _ in breakpoints}
    assert text == ASSISTANT.prompts[-1], "one cached entry, at the end of the kind's prompts"
    ends = [end for _, end in breakpoints]
    assert [text for text, _ in ends] == [DATA_NOTICE] * 4, "and one at the layer's end"
    assert len({cache for _, cache in ends}) == 3, "the layer's end is each text's own"


# Check 2: only a recorded switch changes it, from the next loop on.


async def test_a_running_session_keeps_its_standing_text_until_a_recorded_switch(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    session_id = await started(loop, MANUAL)
    await asked(loop, session_id, "What is the total?", reply(said("12.")))
    # The organization revises its text: a session made now pins the new
    # one, and the running session's requests stay as they were.
    other = await started(loop, REVISED)
    await asked(loop, session_id, "And the tax?", reply(said("1.2.")))
    before, unchanged = main_calls(loop)
    assert prefix(before) == prefix(unchanged) and MANUAL in unchanged.system[1].text
    (theirs,) = [step for step in await loop.history(other) if step.type is StepType.SWITCHED]
    assert theirs.as_text() == REVISED

    switch = SwitchStanding(id=new_id(), standing=REVISED)
    recorded = await loop.managers.agents.switch_standing(loop.owner, session_id, switch)
    again = await loop.managers.agents.switch_standing(loop.owner, session_id, switch)
    assert again == recorded, "a switch asked again answers the step it recorded"
    assert isinstance(recorded.header, SwitchedHeader)
    assert recorded.header.standing == StandingSwitch(chars=len(REVISED))
    await asked(loop, session_id, "And now?", reply(said("Still 12.")))
    switched = main_calls(loop)[2]
    assert switched.system[1].text == f"{STANDING_LEAD}\n\n{REVISED}"
    assert (switched.tools, switched.system[0]) == (unchanged.tools, unchanged.system[0])
    steps = await loop.history(session_id)
    assert [s.as_text() for s in steps if s.type is StepType.SWITCHED] == [MANUAL, REVISED]
    assert await replayed(loop, session_id) == main_calls(loop), "the steps render the bytes"


def a_standing(history: History, text: str) -> Step:
    return history.add(
        type=StepType.SWITCHED,
        actor=Actor.PERSON,
        origin=Origin.API,
        header=SwitchedHeader(standing=StandingSwitch(chars=len(text))),
        content=Content(blocks=(TextBlock(text=text),) if text else ()),
    )


def test_a_switch_recorded_while_a_loop_runs_waits_for_the_next_loop() -> None:
    history = History()
    a_standing(history, MANUAL)
    objective = history.message("Find the total.")
    history.turn((objective,), "Reading.", [("lookup", "12")])
    a_standing(history, REVISED)
    assert standing_text(history.steps, objective.seq) == MANUAL, "the open loop keeps its own"
    later = history.message("And the tax?")
    assert standing_text(history.steps, later.seq) == REVISED, "the next loop takes the switch"
    a_standing(history, "")
    assert standing_text(history.steps, history.steps[-1].seq + 1) == "", "a switch to none"
    assert standing_text([], 1) == "", "a session that pinned none"


async def test_a_switch_arrives_through_the_inbox_and_says_nothing_of_a_run(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path)
    session_id = await started(loop, "")
    await loop.say(session_id, "What is the total?")
    switch = SwitchStanding(id=new_id(), standing=MANUAL)
    await loop.managers.agents.switch_standing(loop.owner, session_id, switch)
    session = await loop.managers.agent_sessions.project_status(loop.owner, session_id)
    assert session.status is SessionStatus.PENDING, "still waiting for its run"
    loop.anthropic.add(reply(said("12.")))
    run = await loop.loops.run(loop.owner, session_id)
    assert (run.end, run.outcome) == (RunEnd.ENDED, LoopOutcome.SUCCEEDED)
    await asked(loop, session_id, "And the tax?", reply(said("1.2.")))
    began, next_loop = main_calls(loop)
    assert len(began.system) == 2, "its loop began from the input before the switch"
    assert next_loop.system[1].text.endswith(MANUAL), "the next loop takes it"
    # A switch instructs: whoever records one may make every call the
    # session's registry offers, as for a message.
    stored = make_session().model_copy(update={"tools": ("lookup", "grant_admin")})
    await loop.managers.agent_sessions.create_session(loop.owner, stored)
    with pytest.raises(NotAuthorized, match="grant_admin"):
        await loop.managers.agents.switch_standing(loop.owner, stored.id, switch)


# Check 3: past its bound, refused when it is pinned.


@pytest.mark.parametrize(
    "build",
    [
        lambda text: Start(id=new_id(), kind="assistant", title="t", standing=text),
        lambda text: Spawn(id=new_id(), kind="assistant", title="t", objective="o", standing=text),
        lambda text: Handoff(
            id=new_id(), kind="assistant", title="t", objective="o", standing=text
        ),
        lambda text: SwitchStanding(id=new_id(), standing=text),
    ],
    ids=["start", "spawn", "handoff", "switch"],
)
def test_standing_text_past_its_bound_is_refused_when_it_is_pinned_with_the_reason(
    build: Callable[[str], BaseModel],
) -> None:
    build("x" * MAX_STANDING)
    with pytest.raises(ValidationError) as refused:
        build("x" * (MAX_STANDING + 1))
    reason = f"standing text of {MAX_STANDING + 1} characters is past its bound of {MAX_STANDING}"
    assert reason in str(refused.value)


def test_a_switch_holds_text_only_when_it_pins_standing_text() -> None:
    good = a_standing(History(), MANUAL)
    assert shape_refusal(good) is None
    miscounted = good.model_copy(
        update={"header": SwitchedHeader(standing=StandingSwitch(chars=3))}
    )
    assert shape_refusal(miscounted) == "it holds its standing text of 3 characters as one block"
    with pytest.raises(ValueError, match="names one of"):
        SwitchedHeader()
