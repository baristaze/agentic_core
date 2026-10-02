"""The session runner over memory: the handler of `LOOP`, the claim loop
that runs a woken session's loop to its end, the kinds each worker
claims, and the knobs it reads."""

import asyncio
import re
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from runner_support import ABSENT, answers

from acme.infra.impl.local import InfraLocalImpl
from acme.integrations.identity.absent import IdentityProviderAbsentImpl
from acme.integrations.impl.configured import IntegrationsOverImpl
from acme.integrations.model_providers.registry import scripted_model_providers
from acme.integrations.model_providers.types import ProviderName
from acme.om.agent_sessions.types.agent_session import AgentSession, SessionStatus
from acme.om.agents import LoopManagerInterface
from acme.om.agents.types.request import Start
from acme.om.agents.types.run import LoopRun, RunEnd
from acme.om.base import new_id, utcnow
from acme.om.context import AppContext, AppType, RequestContext, TenantContext
from acme.om.exceptions import NotFound
from acme.om.steps.rules import message_step
from acme.om.steps.types.header import LoopOutcome
from acme.om.steps.types.step import StepType
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.om.tenancy.rules import ROLE_PERMISSIONS
from acme.om.work.types.handler import WorkParked
from acme.om.work.types.work_item import WORK_ENQUEUE_PERMISSIONS, WorkItem, WorkKind
from acme.workers.maintenance.container import WorkerContainer
from acme.workers.maintenance.main import build_loop
from acme.workers.session_runner.container import RunnerContainer
from acme.workers.session_runner.main import build_runner, loop_options
from acme.workers.session_runner.runs import LoopHandlerImpl
from acme.workers.session_runner.settings import SessionRunnerSettings

ENV_EXAMPLE = Path(__file__).resolve().parents[3] / ".env.example"
APP = AppContext(type=AppType.PORTAL, version="portal@test")


def settings(**overrides: object) -> SessionRunnerSettings:
    return SessionRunnerSettings.model_validate(
        {"_env_file": None, "environment": "test", "runner_id": "runner-test", **overrides}
    )


class Answering(LoopManagerInterface):
    """A loop whose every run ends as the case says; nothing else is
    reached."""

    def __init__(self, answer: Callable[[UUID], LoopRun]) -> None:
        self._answer = answer

    async def run(self, ctx: TenantContext, session_id: UUID) -> LoopRun:
        return self._answer(session_id)


Answering.__abstractmethods__ = frozenset()


def an_item(ctx: TenantContext, session_id: UUID) -> WorkItem:
    now = utcnow()
    return WorkItem(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
        kind=WorkKind.LOOP,
        target_id=session_id,
        idempotency_key=new_id(),
        request_id=new_id(),
        available_at=now,
    )


def ending(end: RunEnd) -> Callable[[UUID], LoopRun]:
    return lambda session_id: LoopRun(session_id=session_id, epoch=3, end=end)


@pytest.mark.parametrize("end", [RunEnd.ENDED, RunEnd.PARKED, RunEnd.STALE, RunEnd.IDLE])
async def test_a_run_that_stops_completes_its_item(tmp_path: Path, end: RunEnd) -> None:
    ctx = (await signed_in(tmp_path))[1]
    handler = LoopHandlerImpl(Answering(ending(end)))  # pyright: ignore[reportAbstractUsage]
    await handler.handle(ctx, an_item(ctx, new_id()))


async def test_a_run_whose_time_is_up_hands_its_item_back_at_once(tmp_path: Path) -> None:
    ctx = (await signed_in(tmp_path))[1]
    handler = LoopHandlerImpl(Answering(ending(RunEnd.YIELDED)))  # pyright: ignore[reportAbstractUsage]

    with pytest.raises(WorkParked) as parked:
        await handler.handle(ctx, an_item(ctx, new_id()))

    assert parked.value.resume_after == timedelta(0), "the next run goes on at once"


async def test_a_session_that_is_gone_has_nothing_to_run(tmp_path: Path) -> None:
    ctx = (await signed_in(tmp_path))[1]

    def gone(session_id: UUID) -> LoopRun:
        raise NotFound(f"agent session {session_id} not found")

    handler = LoopHandlerImpl(Answering(gone))  # pyright: ignore[reportAbstractUsage]
    await handler.handle(ctx, an_item(ctx, new_id()))


def test_every_kind_is_claimed_by_one_worker_and_asked_for_as_widely_as_it_runs(
    tmp_path: Path,
) -> None:
    """The maintenance worker and the runner split the kinds between them,
    and whoever may ask for the loop's work may make every call its handler
    makes."""
    worker = WorkerContainer.for_tests(StorageMemoryImpl(), InfraLocalImpl(tmp_path / "worker"))
    maintenance = set(build_loop(worker).kinds)
    runner = runner_over(tmp_path)
    handlers = build_runner(runner)._handlers  # pyright: ignore[reportPrivateUsage]
    assert set(handlers) == {WorkKind.LOOP} and not maintenance & set(handlers)
    assert maintenance | set(handlers) == set(WorkKind)
    asking = WORK_ENQUEUE_PERMISSIONS[WorkKind.LOOP]
    for role, permissions in ROLE_PERMISSIONS.items():
        if asking in permissions:
            missing = [p for p in LoopHandlerImpl.REQUIRES if p not in permissions]
            assert not missing, f"{role.value} asks for LOOP without {missing}"


def test_the_runner_sweeps_recovery_alone_on_knobs_of_its_own() -> None:
    options = loop_options(settings(runner_lease_seconds=7, runner_lane="loops"))
    assert options.recovery_only, "the runner purges nothing and judges no tenant purged"
    assert (options.lease, options.lane, options.worker_id) == (
        timedelta(seconds=7),
        "loops",
        "runner-test",
    )


def test_every_knob_of_the_runner_is_in_the_example_env() -> None:
    """Each of the runner's own fields is documented beside the others, under
    its prefix; the ones it shares are the other processes'."""
    text = ENV_EXAMPLE.read_text()
    own = [name for name in SessionRunnerSettings.model_fields if name.startswith("runner_")]
    assert own
    for name in own:
        assert re.search(rf"^#?ACME_{name.upper()}=", text, re.MULTILINE), name


def runner_over(tmp_path: Path) -> RunnerContainer:
    providers = scripted_model_providers()
    return RunnerContainer.over(
        settings(),
        StorageMemoryImpl(),
        InfraLocalImpl(tmp_path),
        IntegrationsOverImpl(IdentityProviderAbsentImpl(), providers),
        agent_kinds=ABSENT,
    )


async def signed_in(tmp_path: Path) -> tuple[RunnerContainer, TenantContext]:
    container = runner_over(tmp_path)
    owner, _ = await container.managers.tenancy.bootstrap(
        RequestContext(request_id=new_id(), app=APP), "Ajax", "ajax", "ann@example.test", "Ann"
    )
    return container, owner


async def settled(container: RunnerContainer, ctx: TenantContext, session_id: UUID) -> AgentSession:
    """The session once its status stops moving: idle or parked."""
    for _ in range(500):
        session = await container.managers.agent_sessions.get_session(ctx, session_id)
        if session.status in (SessionStatus.IDLE, SessionStatus.PARKED):
            return session
        await asyncio.sleep(0.01)
    raise AssertionError(f"session {session_id} never settled")


async def test_the_runner_claims_a_woken_sessions_loop_and_runs_it_to_its_end(
    tmp_path: Path,
) -> None:
    container, owner = await signed_in(tmp_path)
    twin = container.integrations.get_model_providers().get(ProviderName.ANTHROPIC)
    twin.add(answers("It drops it when the grip is released early."))  # pyright: ignore[reportAttributeAccessIssue]
    managers = container.managers
    session = await managers.agents.start_session(
        owner, Start(id=new_id(), kind="assistant", title="the dropped object")
    )
    said = message_step(new_id(), utcnow(), session.id, owner, "Why does it drop the object?")
    await managers.agent_sessions.receive(owner, session.id, [said])
    runner = build_runner(container)
    running = asyncio.create_task(runner.run())
    try:
        ended = await settled(container, owner, session.id)
    finally:
        runner.stop()
        await running

    assert ended.status is SessionStatus.IDLE
    page = await managers.steps.get_steps(owner, session.id, 0, 50)
    assert [step.type for step in page.items] == [
        StepType.MESSAGE,
        StepType.MODEL_REQUEST,
        StepType.MODEL_RESPONSE,
        StepType.LOOP_ENDED,
    ]
    assert page.items[-1].header.outcome is LoopOutcome.SUCCEEDED  # pyright: ignore[reportAttributeAccessIssue]
