"""A child's report through the loop: when its loop ends, or parks on its
person, its parent's inbox holds an agent's message that names it, says
how its loop stands and what it said last, carries its mark, and is data
in its parent's next request. Every report wakes the parent but the note
of a cancel the parent sent down."""

from pathlib import Path
from uuid import UUID

import pytest
from contracts.loops import LEAD, WORKER, Loop, call, loop_over, reply, said, use

from acme.om.agent_sessions.limits import deadline_park
from acme.om.agent_sessions.rules import QUESTION
from acme.om.agent_sessions.types.agent_session import SessionStatus
from acme.om.agents import loop_rules as rules
from acme.om.agents.rules import notes_parent
from acme.om.agents.types.request import Spawn
from acme.om.agents.types.run import RunEnd
from acme.om.attribution.rules import trust_of
from acme.om.attribution.types.authority import Trust
from acme.om.base import new_id
from acme.om.steps.types.content import TextBlock, ToolUseBlock
from acme.om.steps.types.header import (
    ControlCommand,
    ControlHeader,
    InputHeader,
    LoopOutcome,
    Park,
    ParkReason,
)
from acme.om.steps.types.step import Actor, Origin, Step, StepType

KINDS = (LEAD, WORKER)


async def a_lead(loop: Loop) -> UUID:
    """A root whose person spoke to it and whose turn ended, as a parent is
    once it delegates: it, and every child it spawns, pays as its person
    pays."""
    parent_id = await loop.start(LEAD.name)
    await loop.say(parent_id, "What is the quarterly total?")
    loop.anthropic.add(reply(said("A sub-agent will find it.")))
    await loop.loops.run(loop.owner, parent_id)
    return parent_id


async def reported(loop: Loop, parent_id: UUID, before: int) -> Step:
    """The one step the parent's inbox took after its first `before`."""
    (step,) = (await loop.history(parent_id))[before:]
    return step


async def a_child(loop: Loop, parent_id: UUID, title: str = "the total") -> UUID:
    child = await loop.managers.agents.spawn(
        loop.owner,
        parent_id,
        Spawn(id=new_id(), kind=WORKER.name, title=title, objective="Find the quarterly total."),
    )
    return child.id


def submit(evidence: Step) -> ToolUseBlock:
    return call("submit", claim="succeeded", evidence=[str(evidence.id)])


def report_of(step: Step) -> InputHeader:
    header = step.header
    assert isinstance(header, InputHeader)
    return header


async def status_of(loop: Loop, session_id: UUID) -> SessionStatus:
    return (await loop.managers.agent_sessions.project_status(loop.owner, session_id)).status


def cancel(loop: Loop, session_id: UUID) -> Step:
    """A person's cancel, through the portal."""
    return Step(
        id=new_id(),
        created_at=loop.clock(),
        session_id=session_id,
        loop_id=new_id(),
        type=StepType.CONTROL,
        actor=Actor.PERSON,
        origin=Origin.PORTAL,
        header=ControlHeader(command=ControlCommand.CANCEL),
    )


async def test_a_child_that_ends_with_a_result_wakes_its_parent_with_its_report_as_data(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path, kinds=KINDS)
    parent_id = await a_lead(loop)
    before = len(await loop.history(parent_id))
    child_id = await a_child(loop, parent_id)
    (objective,) = await loop.history(child_id)
    loop.anthropic.add(reply(said("The total is 12."), submit(objective)))

    run = await loop.loops.run(loop.owner, child_id)

    assert run.outcome is LoopOutcome.SUCCEEDED
    report = await reported(loop, parent_id, before)
    header = report_of(report)
    assert (report.type, report.actor, header.waking) == (StepType.MESSAGE, Actor.AGENT, True)
    assert header.agent is not None and header.agent.session_id == child_id
    assert trust_of(report) is Trust.DATA, "a child's words never instruct its parent"
    text = report.as_text()
    assert f"Sub-agent {child_id}" in text and "ended succeeded" in text
    assert "accepted, unverified" in text and "The total is 12." in text
    assert await status_of(loop, parent_id) is SessionStatus.PENDING

    loop.anthropic.add(reply(said("The quarterly total is 12.")))
    await loop.loops.run(loop.owner, parent_id)

    (rendered,) = [
        block.text
        for message in loop.anthropic.calls[-1].messages
        for block in message.blocks
        if isinstance(block, TextBlock) and block.text.startswith("<data")
    ]
    assert rendered.startswith('<data origin="message"') and 'actor="agent"' in rendered
    assert f"Sub-agent {child_id}" in rendered


async def test_a_report_ended_again_by_a_new_run_is_written_once(tmp_path: Path) -> None:
    """A run lost after the report and before the loop closed leaves the loop
    open; the run that ends it again repeats the report under its id."""
    loop = loop_over(tmp_path, kinds=KINDS)
    parent_id = await a_lead(loop)
    before = len(await loop.history(parent_id))
    child_id = await a_child(loop, parent_id)
    (objective,) = await loop.history(child_id)
    loop.anthropic.add(reply(said("The total is 12."), submit(objective)))
    await loop.loops.run(loop.owner, child_id)
    report = await reported(loop, parent_id, before)
    steps = await loop.history(child_id)
    opened = rules.OpenLoop(objective.id, objective.seq, None)
    again = rules.report_of(steps, opened, outcome=LoopOutcome.SUCCEEDED)

    stored = await loop.managers.agents.report_to_parent(loop.owner, child_id, again)

    assert stored is not None and stored.id == report.id
    assert await reported(loop, parent_id, before) == report


async def test_a_child_that_read_data_marks_its_parent_when_its_report_arrives(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path, kinds=KINDS)
    attribution = loop.managers.attribution
    parent_id = await a_lead(loop)
    before = len(await loop.history(parent_id))
    child_id = await a_child(loop, parent_id)
    (objective,) = await loop.history(child_id)
    loop.anthropic.add(reply(use("lookup")), reply(said("The total is 12."), submit(objective)))
    assert not await attribution.is_marked(loop.owner, parent_id)

    await loop.loops.run(loop.owner, child_id)

    assert await attribution.is_marked(loop.owner, child_id), "it read a tool's output"
    report = await reported(loop, parent_id, before)
    assert report_of(report).untrusted, "the report carries the child's mark"
    assert await attribution.is_marked(loop.owner, parent_id)


async def test_a_child_that_asks_its_person_notes_its_parent_and_wakes_it(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path, kinds=KINDS)
    parent_id = await a_lead(loop)
    before = len(await loop.history(parent_id))
    child_id = await a_child(loop, parent_id)
    loop.anthropic.add(reply(said("Which quarter?"), call("ask_person", question="Which one?")))

    run = await loop.loops.run(loop.owner, child_id)

    assert run.end is RunEnd.PARKED and run.park == QUESTION
    note = await reported(loop, parent_id, before)
    assert report_of(note).waking is True
    assert "waits for a person (answer)" in note.as_text() and "Which quarter?" in note.as_text()
    assert await status_of(loop, parent_id) is SessionStatus.PENDING


async def test_a_cancelled_child_notes_its_parent_and_wakes_it_unless_the_parent_sent_it(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path, kinds=KINDS)
    by_person, by_parent = await a_lead(loop), await a_lead(loop)
    before = len(await loop.history(by_person))
    stopped = await a_child(loop, by_person)
    await loop.managers.steps.append_inputs(loop.owner, stopped, [cancel(loop, stopped)])
    cascaded = await a_child(loop, by_parent)
    assert await loop.managers.agents.cancel_children(loop.owner, by_parent) == (cascaded,)

    for child_id in (stopped, cascaded):
        run = await loop.loops.run(loop.owner, child_id)
        assert run.outcome is LoopOutcome.CANCELLED

    noted = await reported(loop, by_person, before)
    assert report_of(noted).waking is True and "ended cancelled" in noted.as_text()
    assert await status_of(loop, by_person) is SessionStatus.PENDING
    quiet = await reported(loop, by_parent, before)
    assert report_of(quiet).waking is False and "ended cancelled" in quiet.as_text()
    assert await status_of(loop, by_parent) is SessionStatus.IDLE, "its parent stopped it"


@pytest.mark.parametrize(
    ("park", "noted"),
    [
        (QUESTION, True),
        (Park(reason=ParkReason.PERSON, unlock="approval"), True),
        (deadline_park(), False),
        (Park(reason=ParkReason.PROVIDER, unlock="anthropic"), False),
        (Park(reason=ParkReason.BUDGET, unlock="budget"), False),
    ],
)
def test_a_park_on_a_person_reaches_the_parent_and_the_trees_waits_do_not(
    park: Park, noted: bool
) -> None:
    assert notes_parent(park) is noted
