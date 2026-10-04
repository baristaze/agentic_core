"""Parking over the memory storage: a park records its reason, its unlock,
and its retry time; one with a retry time is woken by itself when that time
comes, and a raised budget wakes the sessions parked on a budget. A woken
session is pending, and a new run writes its `resumed` step."""

from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from contracts.agent_session_storage import make_session
from contracts.budget_storage import make_budget
from contracts.doubles import context
from contracts.step_storage import make_message, make_request
from contracts.tools import stand_ins

from acme.infra.impl.local import InfraLocalImpl
from acme.om.agent_sessions.limits import step_guard_park
from acme.om.agent_sessions.types.agent_session import AgentSession, SessionStatus
from acme.om.base import new_id, utcnow
from acme.om.budgets.rules import HOLD_RETRY, budget_park, window_bounds
from acme.om.budgets.types.amount import Amount, AmountUnit, Spend
from acme.om.budgets.types.breach import Breach, BreachAction, Refusal
from acme.om.budgets.types.budget import BudgetScope, BudgetScopeKind, WindowKind
from acme.om.budgets.types.hold import Billed, Hold, HoldRequest
from acme.om.context import Role, TenantContext
from acme.om.exceptions import StaleWriter
from acme.om.root import Managers, build_managers
from acme.om.steps.types.header import (
    ControlCommand,
    ControlHeader,
    Park,
    ParkedHeader,
    ParkReason,
)
from acme.om.steps.types.step import Actor, StepType
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.om.work.storage.impl.memory import WorkStorageMemoryImpl
from acme.om.work.types.work_item import WakeSessionPayload, WorkItem, WorkKind

# What `make_session` names, so the agents manager classes every tool it
# offers.
TOOLS = stand_ins("read_log", "run_tests")


class Engine:
    """The managers over one memory storage, and what its queue holds."""

    def __init__(self, tmp_path: Path) -> None:
        self.storage = StorageMemoryImpl()
        self.managers: Managers = build_managers(
            self.storage, InfraLocalImpl(tmp_path), tool_catalog=TOOLS
        )

    def queued(self, kind: WorkKind) -> list[WorkItem]:
        """The items of a kind in the queue, whatever their time: the worker
        suite claims them, under the person who asked."""
        work = self.storage.get_work_storage()
        assert isinstance(work, WorkStorageMemoryImpl)
        items = [item for _, item in work._items.values()]  # pyright: ignore[reportPrivateUsage]
        return [item for item in items if item.kind is kind]


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return Engine(tmp_path)


async def running(engine: Engine, ctx: TenantContext) -> tuple[AgentSession, int, UUID]:
    """A session whose loop a run holds: a message woke it, a run took its
    epoch and asked the model."""
    sessions, steps = engine.managers.agent_sessions, engine.managers.steps
    session = await sessions.create_session(ctx, make_session())
    (message,) = await steps.append_inputs(ctx, session.id, [make_message(session.id)])
    epoch = await steps.begin_run(ctx, session.id)
    await steps.append_steps(
        ctx, session.id, epoch, [make_request(session.id, message.id, (message.id,))]
    )
    return await sessions.project_status(ctx, session.id), epoch, message.id


async def test_a_park_records_its_reason_unlock_and_retry_time(engine: Engine) -> None:
    ctx = context(Role.MEMBER)
    session, epoch, loop = await running(engine, ctx)
    park = Park(
        reason=ParkReason.PROVIDER, unlock="anthropic", retry_at=utcnow() + timedelta(minutes=5)
    )
    parked = await engine.managers.agent_sessions.park(ctx, session.id, epoch, loop, park)
    assert (parked.status, parked.park) == (SessionStatus.PARKED, park)
    page = await engine.managers.steps.get_steps(ctx, session.id, 0, 10)
    step = page.items[-1]
    assert step.type is StepType.PARKED and step.loop_id == loop and step.actor is Actor.ENGINE
    assert isinstance(step.header, ParkedHeader) and step.header.park == park
    # A park writes no outcome: the loop is suspended, not ended.
    assert all(s.type is not StepType.LOOP_ENDED for s in page.items)


async def test_a_park_with_a_retry_time_waits_in_the_queue_until_it(engine: Engine) -> None:
    ctx = context(Role.MEMBER)
    session, epoch, loop = await running(engine, ctx)
    later = utcnow() + timedelta(hours=1)
    park = Park(reason=ParkReason.PROVIDER, unlock="anthropic", retry_at=later)
    await engine.managers.agent_sessions.park(ctx, session.id, epoch, loop, park)
    (wake,) = engine.queued(WorkKind.WAKE_SESSION)
    assert (wake.target_id, wake.available_at, wake.created_by) == (
        session.id,
        later,
        session.created_by,
    )
    assert WakeSessionPayload.model_validate(dict(wake.payload)).park == park


async def test_a_park_resumes_by_itself_once_its_retry_time_comes(engine: Engine) -> None:
    ctx = context(Role.MEMBER)
    session, epoch, loop = await running(engine, ctx)
    # A refusal whose window resets now: the retry time has come.
    reset = utcnow() - timedelta(seconds=1)
    park = budget_park(refusal_resetting_at(reset), utcnow())
    await engine.managers.agent_sessions.park(ctx, session.id, epoch, loop, park)
    (item,) = engine.queued(WorkKind.WAKE_SESSION)
    assert item.available_at <= utcnow(), "its time has come"
    payload = WakeSessionPayload.model_validate(dict(item.payload))
    woken = await engine.managers.agent_sessions.wake_session(ctx, item.target_id, payload.park)
    assert (woken.status, woken.park) == (SessionStatus.PENDING, None)
    unlock = (await engine.managers.steps.get_steps(ctx, session.id, 0, 10)).items[-1]
    assert (
        isinstance(unlock.header, ControlHeader) and unlock.header.command is ControlCommand.UNLOCK
    )
    assert unlock.actor is Actor.ENGINE
    # A new run takes it up; its gates run again before its next call.
    epoch = await engine.managers.steps.begin_run(ctx, session.id)
    resumed = await engine.managers.agent_sessions.resume(ctx, session.id, epoch, loop)
    assert resumed.status is SessionStatus.RUNNING
    # The wake ran once; a second run of it finds the session moved on.
    again = await engine.managers.agent_sessions.wake_session(ctx, session.id, payload.park)
    assert again == resumed


async def test_a_park_only_a_person_clears_asks_for_no_wake(engine: Engine) -> None:
    ctx = context(Role.MEMBER)
    session, epoch, loop = await running(engine, ctx)
    parked = await engine.managers.agent_sessions.park(
        ctx, session.id, epoch, loop, step_guard_park()
    )
    assert parked.park is not None and parked.park.retry_at is None
    assert engine.queued(WorkKind.WAKE_SESSION) == []
    # Nothing but its park's own wake unlocks it.
    budget_wake = Park(reason=ParkReason.BUDGET, unlock="b", retry_at=utcnow())
    assert await engine.managers.agent_sessions.wake_session(ctx, session.id, budget_wake) == parked


async def test_a_stale_run_parks_nothing(engine: Engine) -> None:
    ctx = context(Role.MEMBER)
    session, lost, loop = await running(engine, ctx)
    await engine.managers.steps.begin_run(ctx, session.id)
    with pytest.raises(StaleWriter):
        await engine.managers.agent_sessions.park(ctx, session.id, lost, loop, step_guard_park())
    assert (
        await engine.managers.agent_sessions.get_session(ctx, session.id)
    ).status is SessionStatus.RUNNING


def refusal_resetting_at(reset: datetime) -> Refusal:
    return Refusal(
        breaches=(
            Breach(
                budget_id=new_id(),
                scope=None,
                unit=AmountUnit.COST,
                amount=1,
                committed=1,
                held=0,
                exposure=1,
                action=BreachAction.RAISE,
                needed=2,
                resets_at=reset,
            ),
        )
    )


async def test_a_call_no_window_holds_parks_for_a_raise_which_wakes_it(engine: Engine) -> None:
    """The gate refuses a call whose worst case alone passes the line, so no
    reset clears it: the loop parks with no time to try again. A person's
    raise wakes every session parked on a budget, and none parked for
    anything else."""
    admin = context(Role.ADMIN)
    managers = engine.managers
    tenant = BudgetScope(kind=BudgetScopeKind.TENANT, key=str(admin.org_id))
    line = await managers.budgets.create_budget(
        admin, make_budget(tenant.kind, tenant.key, cost_micros=500)
    )
    asked = HoldRequest(
        spender_id=admin.user_id,
        scopes=(tenant,),
        exposure=Spend(cost_micros=800, tokens=10),
        purpose="main",
    )
    on_budget: list[UUID] = []
    for _ in range(2):
        session, epoch, loop = await running(engine, admin)
        refusal = await managers.budget_gate.authorize(admin, asked)
        assert isinstance(refusal, Refusal)
        parked = await managers.agent_sessions.park(
            admin, session.id, epoch, loop, budget_park(refusal, utcnow())
        )
        assert parked.park is not None
        assert (parked.park.reason, parked.park.unlock) == (ParkReason.BUDGET, str(line.id))
        assert parked.park.retry_at is None, "a fresh window refuses it too"
        on_budget.append(session.id)
    assert engine.queued(WorkKind.WAKE_SESSION) == []
    waiting, epoch, loop = await running(engine, admin)
    await managers.agent_sessions.park(admin, waiting.id, epoch, loop, step_guard_park())

    await managers.budgets.change_amount(admin, line.id, Amount(cost_micros=5_000), line.version)
    (item,) = engine.queued(WorkKind.WAKE_SESSIONS)
    assert (item.target_id, dict(item.payload)) == (admin.org_id, {"reason": "budget"})
    assert await managers.agent_sessions.wake_parked(admin, ParkReason.BUDGET) == 2
    for session_id in on_budget:
        assert (
            await managers.agent_sessions.get_session(admin, session_id)
        ).status is SessionStatus.PENDING
    still = await managers.agent_sessions.get_session(admin, waiting.id)
    assert still.status is SessionStatus.PARKED
    # The woken run asks the gate again, and now it fits.
    assert not isinstance(await managers.budget_gate.authorize(admin, asked), Refusal)


async def test_a_call_open_holds_refuse_wakes_soon_and_fits_once_they_settle(
    engine: Engine,
) -> None:
    """A month's line of 1,000 with 600 held by another session's call
    refuses a call of 500. What the month spent leaves room, so the park
    tries again soon, not at the month's end; the other call settles at 100,
    and the woken loop's gate holds its call."""
    admin = context(Role.ADMIN)
    managers = engine.managers
    tenant = BudgetScope(kind=BudgetScopeKind.TENANT, key=str(admin.org_id))
    line = await managers.budgets.create_budget(
        admin, make_budget(tenant.kind, tenant.key, window=WindowKind.MONTH, cost_micros=1_000)
    )

    def asked(cost: int) -> HoldRequest:
        exposure = Spend(cost_micros=cost, tokens=10)
        return HoldRequest(
            spender_id=admin.user_id, scopes=(tenant,), exposure=exposure, purpose="main"
        )

    other = await managers.budget_gate.authorize(admin, asked(600))
    assert isinstance(other, Hold)
    session, epoch, loop = await running(engine, admin)
    refusal = await managers.budget_gate.authorize(admin, asked(500))
    assert isinstance(refusal, Refusal)
    now = utcnow()
    park = budget_park(refusal, now)
    _, month_end = window_bounds(line.window, now)
    assert park.retry_at == now + HOLD_RETRY
    assert month_end is not None and now + HOLD_RETRY < month_end
    await managers.agent_sessions.park(admin, session.id, epoch, loop, park)
    (wake,) = engine.queued(WorkKind.WAKE_SESSION)
    assert wake.available_at == park.retry_at

    await managers.budget_gate.settle(
        admin, other.id, Billed(usage=Spend(cost_micros=100, tokens=10))
    )
    woken = await managers.agent_sessions.wake_session(admin, session.id, park)
    assert woken.status is SessionStatus.PENDING
    assert isinstance(await managers.budget_gate.authorize(admin, asked(500)), Hold)
