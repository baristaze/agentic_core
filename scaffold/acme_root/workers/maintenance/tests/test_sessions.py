"""Parked agent sessions in the worker: a park whose retry time comes is
woken from the queue by itself, a raised budget wakes the sessions parked on
a budget, and a wake that finds its session moved on does nothing."""

from datetime import timedelta
from pathlib import Path
from uuid import UUID

from worker_support import build_container, request, sign_in

from acme.om.agent_sessions.types.agent_session import AgentSession, SessionStatus
from acme.om.attribution.types.principal import Principal, PrincipalKind
from acme.om.base import new_id, utcnow
from acme.om.budgets.types.amount import Amount
from acme.om.budgets.types.budget import Budget, BudgetScopeKind, WindowKind
from acme.om.context import TenantContext
from acme.om.steps.types.content import Content, TextBlock
from acme.om.steps.types.header import InputHeader, ModelRequestHeader, Park, ParkReason
from acme.om.steps.types.step import Actor, Origin, Step, StepType
from acme.om.work.types.work_item import WorkItem, WorkKind
from acme.workers.maintenance.container import WorkerContainer
from acme.workers.maintenance.main import build_loop

LEASE = timedelta(seconds=30)
KINDS = [WorkKind.WAKE_SESSION, WorkKind.WAKE_SESSIONS]


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
    sessions, steps = container.managers.agent_sessions, container.managers.steps
    now = utcnow()
    session = await sessions.create_session(
        ctx,
        AgentSession(
            id=new_id(),
            created_at=now,
            updated_at=now,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
            title="a session that waits",
            kind="delivery",
            kind_version=1,
            root_id=new_id(),
        ),
    )
    person = Principal(kind=PrincipalKind.PERSON, id=ctx.user_id)
    message_id = new_id()
    message = Step(
        id=message_id,
        created_at=now,
        session_id=session.id,
        loop_id=message_id,
        type=StepType.MESSAGE,
        actor=Actor.PERSON,
        origin=Origin.PORTAL,
        header=InputHeader(principal=person),
        content=Content(blocks=(TextBlock(text="look into it"),)),
    )
    await steps.append_inputs(ctx, session.id, [message])
    epoch = await steps.begin_run(ctx, session.id)
    request_step = Step(
        id=new_id(),
        created_at=now,
        session_id=session.id,
        loop_id=message_id,
        type=StepType.MODEL_REQUEST,
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
    await steps.append_steps(ctx, session.id, epoch, [request_step])
    return await sessions.park(ctx, session.id, epoch, message_id, park)


async def status_of(
    container: WorkerContainer, ctx: TenantContext, session_id: UUID
) -> SessionStatus:
    return (await container.managers.agent_sessions.get_session(ctx, session_id)).status


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
    assert [(item.kind, item.target_id) for item in ran] == [(WorkKind.WAKE_SESSION, woken.id)]
    assert await status_of(container, ctx, woken.id) is SessionStatus.PENDING
    assert await status_of(container, ctx, waiting.id) is SessionStatus.PARKED
    # The wake asked as the person who made the session.
    assert {item.created_by for item in ran} == {ctx.user_id}


async def test_a_raised_budget_wakes_the_sessions_parked_on_a_budget(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    reset = utcnow() + timedelta(days=1)
    on_budget = await parked(
        container, ctx, Park(reason=ParkReason.BUDGET, unlock="b", retry_at=reset)
    )
    on_person = await parked(container, ctx, Park(reason=ParkReason.PERSON, unlock="approval"))
    now = utcnow()
    budget = await container.managers.budgets.create_budget(
        ctx,
        Budget(
            id=new_id(),
            created_at=now,
            updated_at=now,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
            scope_kind=BudgetScopeKind.TENANT,
            scope_key=str(ctx.org_id),
            window_kind=WindowKind.DAY,
            cost_micros=1_000,
        ),
    )
    assert await drain(container) == [], "the budget park waits for its reset"
    await container.managers.budgets.change_amount(ctx, budget.id, Amount(cost_micros=9_000), 1)
    ran = await drain(container)
    assert [(item.kind, item.target_id) for item in ran] == [(WorkKind.WAKE_SESSIONS, ctx.org_id)]
    assert await status_of(container, ctx, on_budget.id) is SessionStatus.PENDING
    assert await status_of(container, ctx, on_person.id) is SessionStatus.PARKED


async def test_a_wake_for_a_session_that_moved_on_does_nothing(tmp_path: Path) -> None:
    container = build_container(tmp_path)
    ctx = await sign_in(container)
    due = Park(
        reason=ParkReason.PROVIDER, unlock="anthropic", retry_at=utcnow() - timedelta(seconds=1)
    )
    session = await parked(container, ctx, due)
    # It was unlocked first; the run that took it up is running.
    sessions = container.managers.agent_sessions
    await sessions.wake_session(ctx, session.id, due)
    history = await container.managers.steps.get_steps(ctx, session.id, 0, 10)
    epoch = await container.managers.steps.begin_run(ctx, session.id)
    running = await sessions.resume(ctx, session.id, epoch, history.items[0].loop_id)
    assert running.status is SessionStatus.RUNNING
    await drain(container)
    after = await sessions.get_session(ctx, session.id)
    assert (after.status, after.status_seq) == (SessionStatus.RUNNING, running.status_seq)
