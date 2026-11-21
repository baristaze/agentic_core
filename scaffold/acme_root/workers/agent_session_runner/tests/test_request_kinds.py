"""A session whose kind's loop runs in the request that asks it is no
worker's. When the request dies mid-loop, its session is left with its
loop open and a call in flight, and neither the runner's recovery nor the
maintenance worker's sweep touches it: no work item is made for it, and
no step is written. A loop item that names it anyway, one already on the
queue, is completed by the runner's claim with no run. The session's next
question takes the loop up."""

import asyncio
from pathlib import Path
from uuid import UUID

from acme.infra.impl.local import InfraLocalImpl
from acme.integrations.identity.absent import IdentityProviderAbsentImpl
from acme.integrations.impl.configured import IntegrationsOverImpl
from acme.integrations.model_providers.registry import scripted_model_providers
from acme.integrations.model_providers.types import ProviderName
from acme.om.agent_sessions.kinds import LOOP_LANE, AgentSessionWorkKind
from acme.om.agent_sessions.limits import Limits
from acme.om.agent_sessions.types.agent_session import AgentSessionStatus
from acme.om.agent_steps.types.agent_step import AgentStepType
from acme.om.agent_steps.types.header import LoopOutcome
from acme.om.agents.types.kind import REQUEST_STEPS, REQUEST_TIME, AgentKind, DoneRule, RunsIn
from acme.om.agents.types.request import Question, Start
from acme.om.agents.types.run import RunEnd
from acme.om.attribution.types.authority import AuthorityMode
from acme.om.base import new_id, utcnow
from acme.om.context import RequestContext, TenantContext
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.om.tools.types.policy import Decision, ToolPolicyLayer, ToolPolicyRule
from acme.om.tools.types.tool import ToolClass
from acme.om.work.storage.impl.memory import WorkStorageMemoryImpl
from acme.om.work.types.work_item import WorkItem, WorkStatus
from acme.workers.agent_session_runner.container import RunnerContainer
from acme.workers.agent_session_runner.main import build_runner
from acme.workers.agent_session_runner.settings import AgentSessionRunnerSettings
from acme.workers.maintenance.container import WorkerContainer
from acme.workers.maintenance.main import build_loop

from contracts.doubles import APP
from contracts.loops import Lookup, reply, said, use

READER = AgentKind(
    name="reader",
    version=1,
    tools=("slow",),
    done_rule=DoneRule.ANSWER,
    authority=AuthorityMode.DELEGATED,
    policy=ToolPolicyLayer(
        rules=(ToolPolicyRule(authorization_class=ToolClass.READ, decision=Decision.ALLOW),)
    ),
    limits=Limits(step_guard=REQUEST_STEPS, run_time=REQUEST_TIME),
    runs_in=RunsIn.REQUEST,
)


async def test_recovery_and_the_sweep_leave_a_request_kinds_session_alone(
    tmp_path: Path,
) -> None:
    storage, infra = StorageMemoryImpl(), InfraLocalImpl(tmp_path)
    providers = scripted_model_providers()
    slow = Lookup("slow", holds=True, workspace=False)
    api = RunnerContainer.over(
        AgentSessionRunnerSettings.model_validate(
            {"_env_file": None, "environment": "test", "runner_id": "runner-test"}
        ),
        storage,
        infra,
        IntegrationsOverImpl(IdentityProviderAbsentImpl(), providers),
        agent_kinds=(READER,),
        tool_catalog=(slow,),
    )
    rctx = RequestContext(request_id=new_id(), app=APP)
    owner, _ = await api.managers.tenancy.bootstrap(rctx, "Ajax", "ajax", "ann@example.test", "Ann")
    agent_session_id = new_id()
    start = Start(id=agent_session_id, kind="reader", title="the dropped object")
    question = Question(id=new_id(), agent_session_id=agent_session_id, text="Why?", start=start)
    await api.managers.agents.ask(owner, question)
    twin = providers.get(ProviderName.ANTHROPIC)
    twin.add(reply(use("slow", q="object 7")))  # pyright: ignore[reportAttributeAccessIssue]

    # The request runs the loop, and dies while its call is in flight.
    request = asyncio.create_task(api.managers.loop.run(owner, agent_session_id))
    await asyncio.wait_for(slow.started.wait(), 5)
    request.cancel()
    await asyncio.gather(request, return_exceptions=True)
    before = await steps_of(api, owner, agent_session_id)
    assert before[-1] == AgentStepType.TOOL_REQUEST, "the loop is open, its call in flight"

    runner = build_runner(api)
    maintenance = build_loop(WorkerContainer.for_tests(storage, infra))
    await runner._recover_once()  # pyright: ignore[reportPrivateUsage]
    await maintenance._sweep_once()  # pyright: ignore[reportPrivateUsage]
    running = asyncio.create_task(runner.run())
    await asyncio.sleep(0.2)
    runner.stop()
    await running

    work = storage.get_work_storage()
    assert isinstance(work, WorkStorageMemoryImpl)
    items = [item for _, item in work._items.values()]  # pyright: ignore[reportPrivateUsage]
    assert [i for i in items if i.target_id == agent_session_id] == [], "no work item"
    assert await steps_of(api, owner, agent_session_id) == before, "no step is written"
    session = await api.managers.agent_sessions.get_agent_session(owner, agent_session_id)
    assert session.status is not AgentSessionStatus.IDLE

    # The next question takes the open loop up: the lost call runs again,
    # under its key, and the loop ends.
    slow.release.set()
    twin.add(reply(said("It dropped twice.")))  # pyright: ignore[reportAttributeAccessIssue]
    nxt = Question(id=new_id(), agent_session_id=agent_session_id, text="And?")
    await api.managers.agents.ask(owner, nxt)
    run = await api.managers.loop.run(owner, agent_session_id)
    assert (run.end, run.outcome) == (RunEnd.ENDED, LoopOutcome.SUCCEEDED)


async def test_a_loop_item_for_a_request_kinds_session_is_completed_unrun(
    tmp_path: Path,
) -> None:
    storage, infra = StorageMemoryImpl(), InfraLocalImpl(tmp_path)
    providers = scripted_model_providers()
    api = RunnerContainer.over(
        AgentSessionRunnerSettings.model_validate(
            {"_env_file": None, "environment": "test", "runner_id": "runner-test"}
        ),
        storage,
        infra,
        IntegrationsOverImpl(IdentityProviderAbsentImpl(), providers),
        agent_kinds=(READER,),
        tool_catalog=(Lookup("slow", holds=True, workspace=False),),
    )
    rctx = RequestContext(request_id=new_id(), app=APP)
    owner, _ = await api.managers.tenancy.bootstrap(rctx, "Ajax", "ajax", "ann@example.test", "Ann")
    agent_session_id = new_id()
    start = Start(id=agent_session_id, kind="reader", title="the dropped object")
    question = Question(id=new_id(), agent_session_id=agent_session_id, text="Why?", start=start)
    # The question is written and its request died before its loop ran: the
    # session waits with its input, and a loop item names it.
    await api.managers.agents.ask(owner, question)
    before = await steps_of(api, owner, agent_session_id)
    now = utcnow()
    item = await api.managers.work.enqueue(
        owner,
        WorkItem(
            id=new_id(),
            created_at=now,
            updated_at=now,
            created_by=owner.user_id,
            updated_by=owner.user_id,
            kind=AgentSessionWorkKind.LOOP,
            target_id=agent_session_id,
            idempotency_key=new_id(),
            request_id=rctx.request_id,
            lane=LOOP_LANE,
            available_at=now,
        ),
    )
    work = storage.get_work_storage()
    assert isinstance(work, WorkStorageMemoryImpl)

    def status() -> WorkStatus:
        return work._items[item.id][1].status  # pyright: ignore[reportPrivateUsage]

    runner = build_runner(api)
    running = asyncio.create_task(runner.run())
    try:
        for _ in range(500):
            if status() is WorkStatus.DONE:
                break
            await asyncio.sleep(0.01)
    finally:
        runner.stop()
        await running

    assert status() is WorkStatus.DONE, "the claim completes the item"
    assert await steps_of(api, owner, agent_session_id) == before, "no step is written"
    twin = providers.get(ProviderName.ANTHROPIC)
    assert twin.calls == [], "no model is called"  # pyright: ignore[reportAttributeAccessIssue]


async def steps_of(
    container: RunnerContainer, ctx: TenantContext, agent_session_id: UUID
) -> list[AgentStepType]:
    page = await container.managers.agent_steps.get_agent_steps(ctx, agent_session_id, 0, 200)
    return [step.type for step in page.items]
