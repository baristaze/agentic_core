"""`write_plan` through the loop: each plan the history answers is one
version, the latest renders last in the next request, and a person reads
it through the session's steps. A plan the schema refuses keeps no
version."""

import json
from pathlib import Path

from contracts.loops import ASSISTANT, DELIVERY, HELPER, call, loop_over, reply, said

from acme.om.agents.types.run import RunEnd
from acme.om.steps.types.content import TextBlock
from acme.om.steps.types.header import LoopOutcome, ToolResponseHeader
from acme.om.steps.types.step import Step, StepType
from acme.om.tools.native.write_plan import Plan, current_plan
from acme.om.windows.rules import data_block

FIRST = "1. Read the weekly report.\n2. Fix its total."
SECOND = "1. Read the weekly report: done.\n2. Fix its total."


def kept(steps: list[Step]) -> list[Step]:
    """The answers that kept a plan."""
    return [
        step
        for step in steps
        if isinstance(step.header, ToolResponseHeader) and step.header.failure is None
    ]


async def test_a_plan_is_kept_by_version_rendered_last_next_call_and_read_by_a_person(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path, kinds=(ASSISTANT, DELIVERY, HELPER))
    session_id = await loop.start("helper")
    await loop.say(session_id, "Fix the weekly report.")
    loop.anthropic.add(
        reply(call("write_plan", plan=FIRST)),
        reply(call("write_plan", plan="")),
        reply(call("write_plan", plan=SECOND)),
        reply(said("The total is fixed.")),
    )

    run = await loop.loops.run(loop.owner, session_id)

    assert (run.end, run.outcome) == (RunEnd.ENDED, LoopOutcome.SUCCEEDED)
    first, refused, second, last = loop.anthropic.calls
    assert first.messages[-1].blocks[-1] != data_block("plan", FIRST), "no plan before one"
    for request, plan in ((refused, FIRST), (second, FIRST), (last, SECOND)):
        assert request.messages[-1].role == "user"
        assert request.messages[-1].blocks[-1] == data_block("plan", plan), "the plan, last"
    # A person, a member who is not the session's owner, reads the history.
    page = await loop.managers.steps.get_steps(loop.colleague(), session_id, 0, 200)
    steps = list(page.items)
    one, two = kept(steps)
    assert current_plan(steps) == Plan(version=2, text=SECOND, step_id=two.id)
    assert current_plan([s for s in steps if s.seq <= one.seq]) == Plan(
        version=1, text=FIRST, step_id=one.id
    )
    for answer, plan in ((one, FIRST), (two, SECOND)):
        shown = answer.as_tool_response().parts[0]
        assert isinstance(shown, TextBlock) and json.loads(shown.text) == {"plan": plan}
    assert len([s for s in steps if s.type is StepType.TOOL_RESPONSE]) == 3
