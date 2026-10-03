"""The session runner over memory: the handler of `LOOP`, the claim loop
that runs a woken session's loop to its end, the role a call made on what
a key said runs with, the product's reader `main` hands down, the kinds
each worker claims, and the knobs it reads."""

import asyncio
import json
import re
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from runner_support import ABSENT, SONNET, answers

from acme.infra.impl.local import InfraLocalImpl
from acme.integrations.identity.absent import IdentityProviderAbsentImpl
from acme.integrations.impl.configured import IntegrationsOverImpl
from acme.integrations.model_providers.calls import ModelReply
from acme.integrations.model_providers.content import TextBlock, ToolUseBlock
from acme.integrations.model_providers.registry import scripted_model_providers
from acme.integrations.model_providers.types import ProviderName, StopReason, Usage
from acme.om.agent_sessions.types.agent_session import AgentSession, SessionStatus
from acme.om.agents import LoopManagerInterface
from acme.om.agents.types.kind import NO_WORKSPACE, AgentKind, DoneRule, TreeLimits
from acme.om.agents.types.request import Start
from acme.om.agents.types.run import LoopRun, RunEnd
from acme.om.attribution.types.authority import AuthorityMode
from acme.om.base import Platform, new_id, utcnow
from acme.om.context import (
    AppContext,
    AppType,
    CredentialKind,
    RequestContext,
    Role,
    TenantContext,
)
from acme.om.exceptions import NotFound, UnknownAgentKind
from acme.om.steps.rules import message_step
from acme.om.steps.types.content import Attachment, Children, DocumentBlock
from acme.om.steps.types.content import TextBlock as KeptText
from acme.om.steps.types.header import LoopOutcome, ToolResponseHeader
from acme.om.steps.types.step import StepType
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.om.tenancy.rules import ROLE_PERMISSIONS
from acme.om.tools.attachments import AttachmentReaderInterface, AttachmentText
from acme.om.tools.tool import ToolInterface, ToolRuntime
from acme.om.tools.types.policy import Decision, PolicyLayer, PolicyRule, Target
from acme.om.tools.types.tool import Effect, ToolClass, ToolInput, ToolSpec
from acme.om.work.storage.impl.memory import WorkStorageMemoryImpl
from acme.om.work.types.handler import WorkParked
from acme.om.work.types.work_item import (
    WORK_ENQUEUE_PERMISSIONS,
    WorkItem,
    WorkKind,
    WorkStatus,
)
from acme.workers.maintenance.container import WorkerContainer
from acme.workers.maintenance.main import build_loop
from acme.workers.session_runner import container as runner_container
from acme.workers.session_runner import main as runner_main
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


def handler_over(container: RunnerContainer, answer: Callable[[UUID], LoopRun]) -> LoopHandlerImpl:
    return LoopHandlerImpl(Answering(answer), container.managers.agent_sessions)  # pyright: ignore[reportAbstractUsage]


@pytest.mark.parametrize("end", [RunEnd.ENDED, RunEnd.PARKED, RunEnd.STALE, RunEnd.IDLE])
async def test_a_run_that_stops_completes_its_item(tmp_path: Path, end: RunEnd) -> None:
    container, ctx = await signed_in(tmp_path)
    await handler_over(container, ending(end)).handle(ctx, an_item(ctx, new_id()))


async def test_a_run_whose_time_is_up_hands_its_item_back_at_once(tmp_path: Path) -> None:
    container, ctx = await signed_in(tmp_path)
    handler = handler_over(container, ending(RunEnd.YIELDED))

    with pytest.raises(WorkParked) as parked:
        await handler.handle(ctx, an_item(ctx, new_id()))

    assert parked.value.resume_after == timedelta(0), "the next run goes on at once"


def not_found(session_id: UUID) -> LoopRun:
    raise NotFound(f"agent session {session_id} not found")


def unknown_kind(session_id: UUID) -> LoopRun:
    raise UnknownAgentKind("agent kind assistant v1 is not declared here")


async def test_a_session_that_is_gone_has_nothing_to_run(tmp_path: Path) -> None:
    container, ctx = await signed_in(tmp_path)
    await handler_over(container, not_found).handle(ctx, an_item(ctx, new_id()))


async def test_a_session_whose_kind_this_runner_lacks_fails_its_item(tmp_path: Path) -> None:
    """What a run did not find completes its item only when it is the
    session itself; a session that is there fails it, to be retried."""
    container, ctx = await signed_in(tmp_path)
    session = await container.managers.agents.start_session(
        ctx, Start(id=new_id(), kind="assistant", title="the dropped object")
    )

    with pytest.raises(UnknownAgentKind):
        await handler_over(container, unknown_kind).handle(ctx, an_item(ctx, session.id))


async def test_a_runner_that_lacks_the_kind_leaves_the_loop_to_a_retry(tmp_path: Path) -> None:
    """A runner that does not declare a woken session's kind, as when the API
    knows a kind before the runner does, fails the loop's item with the
    reason, never completes it, so the session is run again once a runner
    declares it."""
    storage, infra = StorageMemoryImpl(), InfraLocalImpl(tmp_path)
    api = RunnerContainer.over(
        settings(),
        storage,
        infra,
        IntegrationsOverImpl(IdentityProviderAbsentImpl(), scripted_model_providers()),
        agent_kinds=ABSENT,
    )
    lacking = RunnerContainer.over(
        settings(),
        storage,
        infra,
        IntegrationsOverImpl(IdentityProviderAbsentImpl(), scripted_model_providers()),
    )
    owner, _ = await api.managers.tenancy.bootstrap(
        RequestContext(request_id=new_id(), app=APP), "Ajax", "ajax", "ann@example.test", "Ann"
    )
    session = await api.managers.agents.start_session(
        owner, Start(id=new_id(), kind="assistant", title="the dropped object")
    )
    said = message_step(new_id(), utcnow(), session.id, owner, "Why does it drop the object?")
    await api.managers.agent_sessions.receive(owner, session.id, [said])
    work = storage.get_work_storage()
    assert isinstance(work, WorkStorageMemoryImpl)

    def loop_items() -> list[WorkItem]:
        items = [item for _, item in work._items.values()]  # pyright: ignore[reportPrivateUsage]
        return [i for i in items if i.kind is WorkKind.LOOP]

    runner = build_runner(lacking)
    running = asyncio.create_task(runner.run())
    try:
        for _ in range(500):
            if any(item.attempts > 0 for item in loop_items()):
                break
            await asyncio.sleep(0.01)
    finally:
        runner.stop()
        await running

    (item,) = loop_items()
    assert item.status is not WorkStatus.DONE and item.attempts >= 1
    assert item.last_error is not None and "UnknownAgentKind" in item.last_error
    pending = await api.managers.agent_sessions.get_session(owner, session.id)
    assert pending.status is SessionStatus.PENDING


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


class Nothing(ToolInput):
    pass


class Seen(Platform):
    role: str


class WhoAmI(ToolInterface):
    """Answers the role its call runs with, and keeps the context."""

    def __init__(self) -> None:
        self.seen: list[TenantContext] = []
        self._spec = ToolSpec(
            name="whoami",
            description="Answers the role the call runs with.",
            input_model=Nothing,
            output_model=Seen,
            timeout=timedelta(seconds=10),
            authorization_class=ToolClass.READ,
            effect=Effect.READ_ONLY,
            interruptible=False,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def target(self, ctx: TenantContext, call_input: ToolInput) -> Target:
        return Target()

    async def preflight(
        self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime
    ) -> None:
        return None

    async def run(
        self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime
    ) -> Platform:
        self.seen.append(ctx)
        return Seen(role=ctx.role.value)


ASKING = AgentKind(
    name="assistant",
    version=1,
    tools=("whoami",),
    done_rule=DoneRule.ANSWER,
    authority=AuthorityMode.DELEGATED,
    tree=TreeLimits(height=1, count=0),
    prompts=("Answer.",),
    policy=PolicyLayer(
        rules=(PolicyRule(authorization_class=ToolClass.READ, decision=Decision.ALLOW),)
    ),
    isolation=NO_WORKSPACE,
)


async def test_a_call_made_on_what_a_key_said_runs_no_higher_than_the_key(
    tmp_path: Path,
) -> None:
    """An owner's key capped at member drives a delegated session: its call
    runs as a member, on the key, never with the owner's role or as the
    system."""
    whoami = WhoAmI()
    container = RunnerContainer.over(
        settings(),
        StorageMemoryImpl(),
        InfraLocalImpl(tmp_path),
        IntegrationsOverImpl(IdentityProviderAbsentImpl(), scripted_model_providers()),
        agent_kinds=(ASKING,),
        tool_catalog=(whoami,),
    )
    managers = container.managers
    rctx = RequestContext(request_id=new_id(), app=APP)
    owner, _ = await managers.tenancy.bootstrap(rctx, "Ajax", "ajax", "ann@example.test", "Ann")
    issued = await managers.tenancy.credentials.create_api_key(owner, "ci", Role.MEMBER)
    program = await managers.tenancy.authenticate(rctx, issued.key)
    twin = container.integrations.get_model_providers().get(ProviderName.ANTHROPIC)
    asks = ToolUseBlock(id=f"use_{new_id().hex[:12]}", name="whoami", input={})
    twin.add(  # pyright: ignore[reportAttributeAccessIssue]
        ModelReply(
            blocks=(TextBlock(text="Checking."), asks),
            stop_reason=StopReason.TOOL_USE,
            usage=Usage(input=10, output=5),
            model=SONNET,
        )
    )
    twin.add(answers("Done."))  # pyright: ignore[reportAttributeAccessIssue]
    session = await managers.agents.start_session(
        program, Start(id=new_id(), kind="assistant", title="who acts")
    )
    said = message_step(new_id(), utcnow(), session.id, program, "Who are you acting as?")
    await managers.agent_sessions.receive(program, session.id, [said])

    runner = build_runner(container)
    running = asyncio.create_task(runner.run())
    try:
        await settled(container, owner, session.id)
    finally:
        runner.stop()
        await running

    (call,) = whoami.seen
    assert program.role is Role.MEMBER
    assert (call.role, call.credential_kind, call.credential_id) == (
        Role.MEMBER,
        CredentialKind.API_KEY,
        issued.api_key.id,
    )


class Reader(AttachmentReaderInterface):
    """A product's reader: the text of the one file it holds, and every
    read it was asked for."""

    def __init__(self, attachment_id: UUID, text: str) -> None:
        self._attachment_id = attachment_id
        self._text = text
        self.asked: list[UUID] = []

    async def read_text(
        self, ctx: TenantContext, session_id: UUID, attachment: Attachment
    ) -> AttachmentText | None:
        self.asked.append(attachment.id)
        return AttachmentText(pages=(self._text,)) if attachment.id == self._attachment_id else None


READING = ASKING.model_copy(update={"tools": ("read_attachment",)})


class Driven:
    """The runner `serve` builds over its container, run through one session
    whose person attached a file, then stopped."""

    def __init__(self, container: RunnerContainer, lane: str | None, file: Attachment) -> None:
        self.container = container
        self.runner = build_runner(container, lane)
        self.file = file
        self.answered: list[str] = []

    def stop(self) -> None:
        self.runner.stop()

    def alive(self) -> bool:
        return self.runner.alive()

    async def run(self) -> None:
        managers = self.container.managers
        owner, _ = await managers.tenancy.bootstrap(
            RequestContext(request_id=new_id(), app=APP), "Ajax", "ajax", "ann@example.test", "Ann"
        )
        twin = self.container.integrations.get_model_providers().get(ProviderName.ANTHROPIC)
        reads = ToolUseBlock(
            id=f"use_{new_id().hex[:12]}",
            name="read_attachment",
            input={"attachment_id": str(self.file.id), "unit": "lines", "first": 2, "last": 2},
        )
        twin.add(  # pyright: ignore[reportAttributeAccessIssue]
            ModelReply(
                blocks=(TextBlock(text="Reading."), reads),
                stop_reason=StopReason.TOOL_USE,
                usage=Usage(input=10, output=5),
                model=SONNET,
            )
        )
        twin.add(answers("The total is 12."))  # pyright: ignore[reportAttributeAccessIssue]
        session = await managers.agents.start_session(
            owner, Start(id=new_id(), kind="assistant", title="the weekly report")
        )
        said = message_step(new_id(), utcnow(), session.id, owner, "Read the file.")
        attached = said.model_copy(
            update={
                "content": said.content.model_copy(
                    update={"blocks": (*said.content.blocks, DocumentBlock(attachment_id=self.file.id))}
                ),
                "children": Children(attachments=(self.file,)),
            }
        )
        await managers.agent_sessions.receive(owner, session.id, [attached])
        running = asyncio.create_task(self.runner.run())
        try:
            await settled(self.container, owner, session.id)
        finally:
            self.runner.stop()
            await running
        page = await managers.steps.get_steps(owner, session.id, 0, 50)
        for step in page.items:
            if isinstance(step.header, ToolResponseHeader):
                parts = step.as_tool_response().parts
                self.answered.extend(p.text for p in parts if isinstance(p, KeptText))


class Quiet:
    """The probe's server, never bound."""

    def __init__(self, *args: object) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


def test_a_runner_booted_through_main_reads_an_attachment_with_the_products_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`main` hands the product's reader down to the managers: the read tool
    answers from it, never from the null that refuses. Only the roots are
    memory ones, and the probe binds no port."""
    file = Attachment(id=new_id(), name="weekly.csv", media_type="text/csv", size=9, hash="k:1")
    reader = Reader(file.id, "week,total\n2026-W39,12\n")
    providers = scripted_model_providers()
    driven: list[Driven] = []

    def driving(container: RunnerContainer, lane: str | None = None) -> Driven:
        driven.append(Driven(container, lane, file))
        return driven[-1]

    monkeypatch.setattr(runner_main, "SessionRunnerSettings", settings)
    monkeypatch.setattr(runner_main, "boot", lambda _: None)
    monkeypatch.setattr(runner_main, "WorkerHttpServer", Quiet)
    monkeypatch.setattr(runner_main, "build_runner", driving)
    monkeypatch.setattr(runner_container, "StoragePostgresImpl", lambda *_, **__: StorageMemoryImpl())
    monkeypatch.setattr(runner_container, "InfraConfiguredImpl", lambda _: InfraLocalImpl(tmp_path))
    monkeypatch.setattr(
        runner_container,
        "IntegrationsConfiguredImpl",
        lambda *_: IntegrationsOverImpl(IdentityProviderAbsentImpl(), providers),
    )

    assert runner_main.main(["serve"], agent_kinds=(READING,), attachment_reader=reader) == 0

    (run,) = driven
    (answer,) = run.answered
    assert json.loads(answer)["text"] == "2026-W39,12"
    assert reader.asked == [file.id]
