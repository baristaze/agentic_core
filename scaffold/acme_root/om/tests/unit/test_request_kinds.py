"""A kind whose loop runs in the request that asks it. Publishing it refuses
one that would hold a worker's work in a request, or run unbounded there,
naming every breach. Its loop never parks and never yields: where another
loop would wait, it ends, and its session asks no worker for a run. It is
never spawned, and nobody takes its environment over."""

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from acme.infra.agent_workspaces import EgressMode, EgressPolicy, IsolationMode, IsolationSpec
from acme.om.agent_sessions.kinds import AgentSessionWorkKind
from acme.om.agent_sessions.limits import Limits
from acme.om.agent_sessions.types.agent_session import AgentSessionStatus
from acme.om.agent_steps.types.agent_step import AgentStepType
from acme.om.agent_steps.types.header import LoopEndedHeader, LoopOutcome
from acme.om.agents.types.kind import (
    REQUEST_STEPS,
    REQUEST_TIME,
    AgentKind,
    AgentKindCatalog,
    DoneRule,
    RunsIn,
)
from acme.om.agents.types.request import Spawn
from acme.om.agents.types.run import RunEnd
from acme.om.attribution.types.authority import AuthorityMode
from acme.om.base import new_id
from acme.om.exceptions import UnsafeConfiguration, ValidationFailed
from acme.om.spend_budgets.types.amount import Amount
from acme.om.tools.native.spawn_sub_agent import SPAWN_SUB_AGENT
from acme.om.work.storage.impl.memory import WorkStorageMemoryImpl

from contracts.loops import ALLOWED, ASSISTANT, Loop, loop_over, reply, said, use

ANSWERER = AgentKind(
    name="answerer",
    version=1,
    tools=("lookup", "slow"),
    done_rule=DoneRule.ANSWER,
    authority=AuthorityMode.DELEGATED,
    share=Amount(tokens=1_000_000),
    policy=ALLOWED,
    limits=Limits(step_guard=REQUEST_STEPS, run_time=REQUEST_TIME),
    runs_in=RunsIn.REQUEST,
)
"""A kind that reads records in the request that asks it."""

CONTAINER = IsolationSpec(mode=IsolationMode.CONTAINER, egress=EgressPolicy(mode=EgressMode.NONE))


@pytest.mark.parametrize(
    ("change", "breach"),
    [
        ({"isolation": CONTAINER}, "it works in a container workspace"),
        (
            {"tools": ("lookup", SPAWN_SUB_AGENT)},
            f"{SPAWN_SUB_AGENT} starts a session, a sub-agent's or a handoff's",
        ),
        ({"tools": ("lookup", "send")}, "send waits for a person's approval"),
        ({"tools": ("lookup", "build")}, "build starts a job, which outlives the request"),
        (
            {"limits": Limits(step_guard=REQUEST_STEPS + 1, run_time=REQUEST_TIME)},
            "its step guard of 11 is past 10 model calls",
        ),
        (
            {"limits": Limits(step_guard=REQUEST_STEPS, run_time=timedelta(seconds=31))},
            "its run time of 31 seconds is past 30 seconds",
        ),
        ({"tools": ("lookup", "absent")}, "absent is no tool of the catalog"),
    ],
)
def test_publishing_a_request_kind_refuses_what_it_breaks(
    tmp_path: Path, change: dict[str, Any], breach: str
) -> None:
    with pytest.raises(UnsafeConfiguration) as refused:
        loop_over(tmp_path, kinds=(ASSISTANT, ANSWERER.model_copy(update=change)))

    assert refused.value.message.startswith("agent kind answerer v1 runs in a request, and ")
    assert breach in refused.value.message


def test_publishing_names_every_breach_and_lets_a_kind_that_breaks_none_through(
    tmp_path: Path,
) -> None:
    broken = ANSWERER.model_copy(update={"isolation": CONTAINER, "limits": Limits(step_guard=50)})

    with pytest.raises(UnsafeConfiguration) as refused:
        loop_over(tmp_path, kinds=(broken,))

    assert "container workspace" in refused.value.message
    assert "step guard of 50" in refused.value.message
    assert "run time of 900 seconds" in refused.value.message
    loop_over(tmp_path, kinds=(ASSISTANT, ANSWERER))
    worker = ANSWERER.model_copy(update={"isolation": CONTAINER, "runs_in": RunsIn.WORKER})
    loop_over(tmp_path, kinds=(worker,))  # a worker's kind holds what it needs


def test_every_version_of_a_kind_runs_in_one_place() -> None:
    moved = ANSWERER.model_copy(update={"version": 2, "runs_in": RunsIn.WORKER})

    with pytest.raises(ValidationError, match="every version of a kind runs in one place"):
        AgentKindCatalog(kinds=(ANSWERER, moved))


def work_for(loop: Loop, agent_session_id: object) -> list[str]:
    work = loop.storage.get_work_storage()
    assert isinstance(work, WorkStorageMemoryImpl)
    items = [item for _, item in work._items.values()]  # pyright: ignore[reportPrivateUsage]
    return [str(i.kind) for i in items if i.target_id == agent_session_id]


async def test_a_question_asks_no_worker_and_the_request_runs_its_loop(tmp_path: Path) -> None:
    loop = loop_over(tmp_path, kinds=(ASSISTANT, ANSWERER))
    worked = await loop.start("assistant")
    asked = await loop.start("answerer")
    sessions = loop.managers.agent_sessions
    for agent_session_id in (worked, asked):
        await loop.say(agent_session_id, "Why does it drop the object?")
        pending = await sessions.project_status(loop.owner, agent_session_id)
        assert pending.status is AgentSessionStatus.PENDING
    assert work_for(loop, worked) == [AgentSessionWorkKind.LOOP], "a worker's kind is queued"
    assert work_for(loop, asked) == [], "a request kind's is not"

    loop.anthropic.add(reply(use("lookup", q="object 7")), reply(said("It dropped twice.")))
    run = await loop.loops.run(loop.owner, asked)

    assert (run.end, run.outcome) == (RunEnd.ENDED, LoopOutcome.SUCCEEDED)
    assert work_for(loop, asked) == []


async def test_a_run_past_its_run_time_ends_its_loop_where_another_would_yield(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path, kinds=(ANSWERER,))
    asked = await loop.start("answerer")
    await loop.say(asked, "What does the record say?")
    loop.anthropic.add(reply(use("slow", q="object 7")), reply(said("Never sent.")))
    slow = loop.tools["slow"]

    running = asyncio.create_task(loop.loops.run(loop.owner, asked))
    await asyncio.wait_for(slow.started.wait(), 5)
    loop.clock.now += REQUEST_TIME + timedelta(seconds=1)
    slow.release.set()
    run = await running

    assert (run.end, run.outcome) == (RunEnd.ENDED, LoopOutcome.INCONCLUSIVE)
    ended = (await loop.history(asked))[-1]
    assert isinstance(ended.header, LoopEndedHeader)
    assert ended.header.cause == (
        "a request waits for nothing: the run passed its run time of 30 seconds"
    )
    assert loop.anthropic.remaining == 1, "no model call after its time ran out"
    assert work_for(loop, asked) == []


async def test_a_request_kind_is_never_spawned_nor_taken_over(tmp_path: Path) -> None:
    loop = loop_over(tmp_path, kinds=(ASSISTANT, ANSWERER))
    parent = await loop.start("assistant")
    asked = await loop.start("answerer")
    spawn = Spawn(id=new_id(), kind="answerer", title="a child", objective="Read the record.")

    with pytest.raises(ValidationFailed, match="never as a sub-agent"):
        await loop.managers.agents.spawn(loop.owner, parent, spawn)
    with pytest.raises(ValidationFailed, match="no environment to take over"):
        await loop.loops.take_over(loop.owner, asked)

    steps = await loop.history(asked)
    assert all(step.type is not AgentStepType.PARKED for step in steps)
