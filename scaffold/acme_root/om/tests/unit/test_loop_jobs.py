"""A job over the memory storage and the scripted provider: its call parks
the loop on the job, holding nothing; its completion, checked against the
park, wakes the loop, which answers the call from it before any model call;
a completion that never comes ends at the job's deadline, never past the
tree's; cancelling the loop, or any other end of it, cancels the job; a
completion that names another session's job, or one settled already, is
refused and wakes nothing; and a job that spends passes the budget gate
before it starts."""

from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from contracts.budget_storage import make_budget
from contracts.doubles import context
from contracts.factories import make_org
from contracts.loops import ALLOWED, ASSISTANT, DELIVERY, Loop, call, loop_over, reply, said

from acme.om.agent_sessions.types.agent_session import SessionStatus
from acme.om.agents.types.kind import AgentKind, DoneRule, TreeLimits
from acme.om.agents.types.run import RunEnd
from acme.om.attribution.types.authority import AuthorityMode
from acme.om.base import new_id
from acme.om.budgets.types.amount import Amount
from acme.om.budgets.types.budget import BudgetScopeKind
from acme.om.context import Role
from acme.om.exceptions import NotFound, UnresolvedRole
from acme.om.steps.types.header import (
    ControlCommand,
    ControlHeader,
    LoopOutcome,
    ParkReason,
    ToolFailure,
)
from acme.om.steps.types.step import Actor, Origin, Step, StepType
from acme.om.tools.types.call import JobCompletion

BUILDER = AgentKind(
    name="builder",
    version=1,
    tools=("lookup", "build", "compute"),
    done_rule=DoneRule.ANSWER,
    authority=AuthorityMode.DELEGATED,
    tree=TreeLimits(height=2, count=4),
    prompts=("You start the work the person asks for, and say how it ended.",),
    policy=ALLOWED,
)


def builder_loop(tmp_path: Path) -> Loop:
    return loop_over(tmp_path, kinds=(ASSISTANT, DELIVERY, BUILDER))


async def parked_on_a_job(loop: Loop, tool: str = "build") -> tuple[UUID, Step]:
    """A session whose loop called `tool` and parked on its job, with the
    call's request."""
    session_id = await loop.start("builder")
    await loop.say(session_id, "Start it.")
    loop.anthropic.add(reply(call(tool, q="everything")))
    run = await loop.loops.run(loop.owner, session_id)
    assert run.end is RunEnd.PARKED and run.park is not None
    assert run.park.reason is ParkReason.JOB, run.park
    (request,) = of_type(await loop.history(session_id), StepType.TOOL_REQUEST)
    return session_id, request


def of_type(steps: list[Step], *types: StepType) -> list[Step]:
    return [step for step in steps if step.type in types]


def answer_to(steps: list[Step], request: Step) -> Step:
    return next(step for step in steps if step.responds_to == request.id)


def text_of(step: Step) -> str:
    return step.as_tool_response().parts[0].text  # type: ignore[union-attr]


def cancel(loop: Loop, session_id: UUID) -> Step:
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


async def test_a_job_parks_its_loop_holding_nothing_and_its_completion_answers_the_call_first(
    tmp_path: Path,
) -> None:
    loop = builder_loop(tmp_path)
    build = loop.jobs["build"]

    session_id, request = await parked_on_a_job(loop)

    # The run has returned while the work goes on: nothing of the loop waits
    # on the job, and the park names it by its key and the tool's handle.
    session = await loop.managers.agent_sessions.get_session(loop.owner, session_id)
    assert session.status is SessionStatus.PARKED and session.park is not None
    assert session.park.job is not None
    assert (session.park.job.key, session.park.job.handle) == (request.id, "build-1")
    assert build.started == {request.id: "build-1"} and not build.cancelled
    assert len(loop.anthropic.calls) == 1

    woken = await loop.loops.complete_job(
        loop.owner,
        session_id,
        JobCompletion(key=request.id, handle="build-1", text="3 targets built"),
    )
    assert woken.status is SessionStatus.PENDING
    loop.anthropic.add(reply(said("It is done.")))

    run = await loop.loops.run(loop.owner, session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED
    steps = await loop.history(session_id)
    answer = answer_to(steps, request)
    assert text_of(answer) == "3 targets built"
    later = [s for s in of_type(steps, StepType.MODEL_REQUEST) if s.seq > request.seq]
    assert answer.seq < later[0].seq, "answered before the next model call"
    sent = str(loop.anthropic.calls[1].model_dump())
    assert sent.count("3 targets built") == 1, "the completion reaches the model once"
    assert build.started == {request.id: "build-1"}, "never started again"


async def test_a_job_that_never_completes_ends_at_its_deadline_never_past_the_trees(
    tmp_path: Path,
) -> None:
    loop = builder_loop(tmp_path)
    build = loop.jobs["build"]
    session_id = await loop.start("builder")
    tree_deadline = loop.clock.now + timedelta(hours=1)  # before the tool's own two hours
    await loop.managers.agents.set_deadline(loop.owner, session_id, tree_deadline)
    await loop.say(session_id, "Start it.")
    loop.anthropic.add(reply(call("build", q="everything")), reply(said("It timed out.")))

    first = await loop.loops.run(loop.owner, session_id)

    assert first.park is not None and first.park.retry_at == tree_deadline
    assert build.deadlines == [tree_deadline], "the work was started to end by it"
    loop.clock.now = tree_deadline
    await loop.managers.agent_sessions.wake_session(loop.owner, session_id, first.park)
    second = await loop.loops.run(loop.owner, session_id)

    (request,) = of_type(await loop.history(session_id), StepType.TOOL_REQUEST)
    answer = answer_to(await loop.history(session_id), request)
    assert getattr(answer.header, "failure", None) is ToolFailure.TIMEOUT
    assert [job.key for job in build.cancelled] == [request.id]
    # The tree's deadline has passed with the job's: the loop waits for a
    # person, and no model call was made after the job's.
    assert second.park is not None and second.park.unlock == "deadline"
    assert len(loop.anthropic.calls) == 1


async def test_cancelling_a_loop_parked_on_a_job_cancels_the_job(tmp_path: Path) -> None:
    loop = builder_loop(tmp_path)
    build = loop.jobs["build"]
    session_id, request = await parked_on_a_job(loop)

    await loop.managers.steps.append_inputs(loop.owner, session_id, [cancel(loop, session_id)])
    run = await loop.loops.run(loop.owner, session_id)

    assert run.outcome is LoopOutcome.CANCELLED
    assert [job.key for job in build.cancelled] == [request.id]
    answer = answer_to(await loop.history(session_id), request)
    assert getattr(answer.header, "failure", None) is ToolFailure.INTERRUPTED
    assert "the job was cancelled" in text_of(answer)


async def test_a_loop_that_ends_any_other_way_while_its_job_works_cancels_the_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A loop that cannot go on when it is woken ends `errored` before any
    call: the job it parked on goes with it."""
    loop = builder_loop(tmp_path)
    build = loop.jobs["build"]
    session_id, request = await parked_on_a_job(loop)

    async def unresolved(*args: object, **kwargs: object) -> None:
        raise UnresolvedRole("the main role resolves no fill")

    monkeypatch.setattr(loop.managers.models, "resolve_fill_set", unresolved)
    await loop.managers.agent_sessions.receive(loop.owner, session_id, [unlock(loop, session_id)])
    run = await loop.loops.run(loop.owner, session_id)

    assert run.outcome is LoopOutcome.ERRORED
    assert [job.key for job in build.cancelled] == [request.id]
    answer = answer_to(await loop.history(session_id), request)
    assert "the loop ended errored" in text_of(answer)


def unlock(loop: Loop, session_id: UUID) -> Step:
    return cancel(loop, session_id).model_copy(
        update={"header": ControlHeader(command=ControlCommand.UNLOCK)}
    )


async def test_a_completion_of_another_sessions_job_or_one_settled_is_refused_and_wakes_nothing(
    tmp_path: Path,
) -> None:
    loop = builder_loop(tmp_path)
    mine, request = await parked_on_a_job(loop)
    theirs, other = await parked_on_a_job(loop)
    before = await loop.history(mine)

    refused = [
        # Another session's job, by its own key and handle.
        (loop.owner, JobCompletion(key=other.id, handle="build-2", text="done")),
        # This session's key with a handle that is not the job's.
        (loop.owner, JobCompletion(key=request.id, handle="build-2", text="done")),
        # Another tenant.
        (context(Role.OWNER, make_org()), JobCompletion(key=request.id, handle="build-1")),
    ]
    for ctx, completion in refused:
        with pytest.raises(NotFound):
            await loop.loops.complete_job(ctx, mine, completion)
    assert await loop.history(mine) == before, "nothing written"
    session = await loop.managers.agent_sessions.get_session(loop.owner, mine)
    assert session.status is SessionStatus.PARKED, "nothing woken"

    completion = JobCompletion(key=request.id, handle="build-1", text="built")
    await loop.loops.complete_job(loop.owner, mine, completion)
    reported = await loop.history(mine)
    with pytest.raises(NotFound):
        await loop.loops.complete_job(loop.owner, mine, completion)
    assert await loop.history(mine) == reported, "a second report adds nothing"
    loop.anthropic.add(reply(said("It is built.")))
    await loop.loops.run(loop.owner, mine)
    settled = await loop.history(mine)
    with pytest.raises(NotFound):
        await loop.loops.complete_job(loop.owner, mine, completion)
    assert await loop.history(mine) == settled, "a settled job takes no report"
    assert text_of(answer_to(settled, request)) == "built"
    still = await loop.managers.agent_sessions.get_session(loop.owner, theirs)
    assert still.status is SessionStatus.PARKED, "the other session waits on its own job"


async def test_a_spending_job_passes_the_budget_gate_first_and_a_refusal_starts_nothing(
    tmp_path: Path,
) -> None:
    loop = builder_loop(tmp_path)
    compute = loop.jobs["compute"]  # 3.6 units an hour, for two hours at most
    session_id = await loop.start("builder")
    line = await loop.managers.budgets.create_budget(
        loop.owner, make_budget(BudgetScopeKind.SESSION, str(session_id), cost_micros=5_000_000)
    )
    await loop.say(session_id, "Start it.")
    loop.anthropic.add(reply(call("compute", q="everything")), reply(said("It is done.")))

    refused = await loop.loops.run(loop.owner, session_id)

    assert refused.park is not None and refused.park.reason is ParkReason.BUDGET
    assert compute.started == {}, "no job started"
    (request,) = of_type(await loop.history(session_id), StepType.TOOL_REQUEST)
    assert not [s for s in await loop.history(session_id) if s.responds_to == request.id]

    raised = Amount(cost_micros=50_000_000)
    await loop.managers.budgets.change_amount(loop.owner, line.id, raised, line.version)
    await loop.managers.agent_sessions.wake_parked(loop.owner, ParkReason.BUDGET)
    started = await loop.loops.run(loop.owner, session_id)

    assert started.park is not None and started.park.job is not None
    assert started.park.job.hold_id is not None
    held = await loop.managers.budgets.get_spend(loop.owner, line.id)
    assert held.held_cost_micros >= 7_200_000, "held at its rate until its deadline"
    await loop.loops.complete_job(
        loop.owner,
        session_id,
        JobCompletion(key=request.id, handle="compute-1", text="done", cost_micros=1_234),
    )
    done = await loop.loops.run(loop.owner, session_id)
    assert done.outcome is LoopOutcome.SUCCEEDED
    spent = await loop.managers.budgets.get_spend(loop.owner, line.id)
    assert spent.held_cost_micros == 0, "the hold settled"
    assert spent.spent_cost_micros >= 1_234
