"""Parked agent sessions in the worker: a park whose retry time comes is
woken from the queue by itself, a raised budget wakes the sessions parked on
a budget, a grant wakes the session parked in line for it and no session
parked for anything else, and a wake that finds its session moved on does
nothing."""

from datetime import timedelta
from pathlib import Path
from uuid import UUID

from worker_support import build_container, request, sign_in

from acme.om.agent_sessions.kinds import AgentSessionWorkKind
from acme.om.agent_sessions.types.agent_session import AgentSession, AgentSessionStatus, Title
from acme.om.agent_steps.types.agent_step import Actor, AgentStep, AgentStepType, Origin
from acme.om.agent_steps.types.content import Content, TextBlock
from acme.om.agent_steps.types.header import (
    InputHeader,
    ModelRequestHeader,
    Park,
    ParkReason,
    WaitLinePark,
)
from acme.om.agents.line_rules import in_line
from acme.om.attribution.types.principal import Principal, PrincipalKind
from acme.om.base import new_id, utcnow
from acme.om.context import TenantContext
from acme.om.leases.types.leasable_resource import LeasableResource, LeasableResourceKind
from acme.om.leases.types.request import LeaseRequest
from acme.om.spend_budgets.types.amount import Amount
from acme.om.spend_budgets.types.spend_budget import SpendBudget, SpendBudgetScopeKind, WindowKind
from acme.om.work.types.work_item import WorkItem
from acme.workers.maintenance.container import WorkerContainer
from acme.workers.maintenance.main import build_loop

LEASE = timedelta(seconds=30)
KINDS = [
    AgentSessionWorkKind.WAKE_AGENT_SESSION,
    AgentSessionWorkKind.WAKE_AGENT_SESSIONS,
    AgentSessionWorkKind.LEASE_NOTICE,
]


async def drain(container: WorkerContainer) -> list[WorkItem]:
    """Claims every available wake and runs it with the worker's own
    handlers, settling it as the loop does."""
    handlers = build_loop(container)._handlers  # pyright: ignore[reportPrivateUsage]
    ran: list[WorkItem] = []
    while True:
        claimed = await container.managers.work.claim(request(), "default", KINDS, "test", LEASE)
        if claimed is None:
            return ran
        ctx, item = claimed
        await handlers[item.kind].handle(ctx, item)
        await container.managers.work.complete(ctx, item)
        ran.append(item)


async def parked(container: WorkerContainer, ctx: TenantContext, park: Park) -> AgentSession:
    """A session a message woke, whose run asked the model and then parked."""
    sessions, steps = container.managers.agent_sessions, container.managers.agent_steps
    now = utcnow()
    session = await sessions.create_agent_session(
        ctx,
        AgentSession(
            id=new_id(),
            created_at=now,
            updated_at=now,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
            title=Title(text="a session that waits"),
            kind="delivery",
            kind_version=1,
            root_id=new_id(),
        ),
    )
    person = Principal(kind=PrincipalKind.PERSON, id=ctx.user_id)
    message_id = new_id()
    message = AgentStep(
        id=message_id,
        created_at=now,
        agent_session_id=session.id,
        loop_id=message_id,
        type=AgentStepType.MESSAGE,
        actor=Actor.PERSON,
        origin=Origin.PORTAL,
        header=InputHeader(principal=person),
        content=Content(blocks=(TextBlock(text="look into it"),)),
    )
    await steps.append_inputs(ctx, session.id, [message])
    epoch = await steps.begin_run(ctx, session.id)
    request_step = AgentStep(
        id=new_id(),
        created_at=now,
        agent_session_id=session.id,
        loop_id=message_id,
        type=AgentStepType.MODEL_REQUEST,
        actor=Actor.ENGINE,
        origin=Origin.ENGINE,
        refs=(message_id,),
        header=ModelRequestHeader(
            role="main",
            spender=person,
            speaker=person,
            fill="anthropic/claude-sonnet-5-5",
            fill_set_version=1,
            left_edge=1,
            prompt_hash="k:prompt",
        ),
    )
    await steps.append_agent_steps(ctx, session.id, epoch, [request_step])
    return await sessions.park(ctx, session.id, epoch, message_id, park)


async def status_of(
    container: WorkerContainer, ctx: TenantContext, agent_session_id: UUID
) -> AgentSessionStatus:
    return (await container.managers.agent_sessions.get_agent_session(ctx, agent_session_id)).status


async def test_a_park_whose_retry_time_came_is_woken_from_the_queue(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    due = Park(
        reason=ParkReason.PROVIDER, unlock="anthropic", retry_at=utcnow() - timedelta(seconds=1)
    )
    later = Park(
        reason=ParkReason.PROVIDER, unlock="anthropic", retry_at=utcnow() + timedelta(hours=1)
    )
    woken, waiting = await parked(container, ctx, due), await parked(container, ctx, later)
    ran = await drain(container)
    assert [(item.kind, item.target_id) for item in ran] == [
        (AgentSessionWorkKind.WAKE_AGENT_SESSION, woken.id)
    ]
    assert await status_of(container, ctx, woken.id) is AgentSessionStatus.PENDING
    assert await status_of(container, ctx, waiting.id) is AgentSessionStatus.PARKED
    # The wake asked as the person who made the session.
    assert {item.created_by for item in ran} == {ctx.user_id}


async def test_a_raised_budget_wakes_the_sessions_parked_on_a_budget(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    reset = utcnow() + timedelta(days=1)
    on_budget = await parked(
        container, ctx, Park(reason=ParkReason.SPEND_BUDGET, unlock="b", retry_at=reset)
    )
    on_person = await parked(container, ctx, Park(reason=ParkReason.PERSON, unlock="approval"))
    now = utcnow()
    budget = await container.managers.spend_budgets.create_spend_budget(
        ctx,
        SpendBudget(
            id=new_id(),
            created_at=now,
            updated_at=now,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
            scope_kind=SpendBudgetScopeKind.TENANT,
            scope_key=str(ctx.org_id),
            window_kind=WindowKind.DAY,
            cost_micros=1_000,
        ),
    )
    assert await drain(container) == [], "the budget park waits for its reset"
    await container.managers.spend_budgets.change_amount(
        ctx, budget.id, Amount(cost_micros=9_000), 1
    )
    ran = await drain(container)
    assert [(item.kind, item.target_id) for item in ran] == [
        (AgentSessionWorkKind.WAKE_AGENT_SESSIONS, ctx.org_id)
    ]
    assert await status_of(container, ctx, on_budget.id) is AgentSessionStatus.PENDING
    assert await status_of(container, ctx, on_person.id) is AgentSessionStatus.PARKED


async def test_a_closed_budget_wakes_the_sessions_parked_on_it_alone(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    now = utcnow()
    budget = await container.managers.spend_budgets.create_spend_budget(
        ctx,
        SpendBudget(
            id=new_id(),
            created_at=now,
            updated_at=now,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
            scope_kind=SpendBudgetScopeKind.TREE,
            scope_key=str(new_id()),
            window_kind=WindowKind.LIFE,
            cost_micros=1_000,
        ),
    )
    on_it = await parked(
        container, ctx, Park(reason=ParkReason.SPEND_BUDGET, unlock=str(budget.id))
    )
    on_another = await parked(container, ctx, Park(reason=ParkReason.SPEND_BUDGET, unlock="b"))
    await container.managers.spend_budgets.close_spend_budget(ctx, budget.id)
    ran = await drain(container)
    assert [(item.kind, item.target_id) for item in ran] == [
        (AgentSessionWorkKind.WAKE_AGENT_SESSIONS, ctx.org_id)
    ]
    assert await status_of(container, ctx, on_it.id) is AgentSessionStatus.PENDING
    assert await status_of(container, ctx, on_another.id) is AgentSessionStatus.PARKED


def an_ask(ctx: TenantContext, leasable_resource_id: UUID) -> LeaseRequest:
    now = utcnow()
    return LeaseRequest(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
        idempotency_key=new_id(),
        kind=LeasableResourceKind.NOOP,
        leasable_resource_id=leasable_resource_id,
    )


async def test_a_grant_wakes_the_session_parked_in_line_and_no_other(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    leases = container.managers.leases
    line = Park(
        reason=ParkReason.LEASABLE_RESOURCE,
        unlock="grant",
        wait_line=WaitLinePark(request_id=new_id(), kind=LeasableResourceKind.NOOP.value, place=1),
    )
    in_the_line = await parked(container, ctx, line)
    on_a_person = await parked(container, ctx, Park(reason=ParkReason.PERSON, unlock="approval"))
    now = utcnow()
    resource = await leases.register(
        ctx,
        LeasableResource(
            id=new_id(),
            created_at=now,
            updated_at=now,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
            kind=LeasableResourceKind.NOOP,
            ref_id=new_id(),
        ),
    )
    held = await leases.ask(ctx, an_ask(ctx, resource.id))
    first, second = [
        await leases.ask(ctx, in_line(an_ask(ctx, resource.id), session.id, new_id()))
        for session in (in_the_line, on_a_person)
    ]
    assert (first.place, second.place) == (1, 2)
    assert held.lease is not None
    await leases.release(ctx, held.lease.id)
    granted = (await leases.get_request(ctx, first.request.id)).lease
    assert granted is not None
    await leases.release(ctx, granted.id)
    ran = await drain(container)
    assert sorted((item.kind, str(item.target_id)) for item in ran) == sorted(
        (AgentSessionWorkKind.LEASE_NOTICE, str(session.id))
        for session in (in_the_line, on_a_person)
    )
    assert await status_of(container, ctx, in_the_line.id) is AgentSessionStatus.PENDING
    assert await status_of(container, ctx, on_a_person.id) is AgentSessionStatus.PARKED


async def test_a_wake_for_a_session_that_moved_on_does_nothing(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    due = Park(
        reason=ParkReason.PROVIDER, unlock="anthropic", retry_at=utcnow() - timedelta(seconds=1)
    )
    session = await parked(container, ctx, due)
    # It was unlocked first; the run that took it up is running.
    sessions = container.managers.agent_sessions
    await sessions.wake_agent_session(ctx, session.id, due)
    history = await container.managers.agent_steps.get_agent_steps(ctx, session.id, 0, 10)
    epoch = await container.managers.agent_steps.begin_run(ctx, session.id)
    running = await sessions.resume(ctx, session.id, epoch, history.items[0].loop_id)
    assert running.status is AgentSessionStatus.RUNNING
    await drain(container)
    after = await sessions.get_agent_session(ctx, session.id)
    assert (after.status, after.status_seq) == (AgentSessionStatus.RUNNING, running.status_seq)
