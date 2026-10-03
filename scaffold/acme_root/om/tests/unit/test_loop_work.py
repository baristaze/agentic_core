"""What asks for a run of a session's loop, and what it runs under: the
inbox that lands a person's message or control, the projection that turns
a session pending and lands the `LOOP` work beside its write, once for
each turn, and the live context of the member a tool call runs for."""

from pathlib import Path
from uuid import UUID

import pytest
from contracts.agent_session_storage import make_session
from contracts.doubles import context
from contracts.step_storage import make_request, make_response

from acme.infra.impl.local import InfraLocalImpl
from acme.om.agent_sessions.rules import QUESTION, asks_for_run, parked_step
from acme.om.agent_sessions.types.agent_session import AgentSession, SessionStatus
from acme.om.agents.loop_rules import ended_step
from acme.om.attribution.impl.manager import members_context
from acme.om.attribution.types.principal import Principal, PrincipalKind
from acme.om.base import new_id, utcnow
from acme.om.context import (
    AppContext,
    AppType,
    CredentialKind,
    Permission,
    RequestContext,
    Role,
    TenantContext,
)
from acme.om.exceptions import NotAuthorized, NotFound
from acme.om.root import Managers, build_managers
from acme.om.steps.rules import control_step, message_step
from acme.om.steps.types.header import (
    ControlCommand,
    InputHeader,
    LoopOutcome,
    Park,
    ParkReason,
)
from acme.om.steps.types.step import Actor, Origin, StepType
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.om.work.storage.impl.memory import WorkStorageMemoryImpl
from acme.om.work.types.work_item import WorkItem, WorkKind

CLI = AppContext(type=AppType.CLI, version="cli@test")


class Engine:
    """The managers over one memory storage, and the loop work its queue
    holds."""

    def __init__(self, tmp_path: Path) -> None:
        self.storage = StorageMemoryImpl()
        self.managers: Managers = build_managers(self.storage, InfraLocalImpl(tmp_path))

    def runs(self, session_id: UUID) -> list[WorkItem]:
        work = self.storage.get_work_storage()
        assert isinstance(work, WorkStorageMemoryImpl)
        items = [item for _, item in work._items.values()]  # pyright: ignore[reportPrivateUsage]
        return [i for i in items if i.kind is WorkKind.LOOP and i.target_id == session_id]

    async def session(self, ctx: TenantContext) -> AgentSession:
        return await self.managers.agent_sessions.create_session(ctx, make_session())

    async def say(self, ctx: TenantContext, session_id: UUID, text: str) -> AgentSession:
        message = message_step(new_id(), utcnow(), session_id, ctx, text)
        (stored,), session = await self.managers.agent_sessions.receive(ctx, session_id, [message])
        assert stored.id == message.id and stored.seq > 0
        return session


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return Engine(tmp_path)


def with_status(status: SessionStatus) -> AgentSession:
    park = (
        Park(reason=ParkReason.PAUSE, unlock="resume") if status is SessionStatus.PARKED else None
    )
    return make_session().model_copy(update={"status": status, "park": park})


@pytest.mark.parametrize(
    ("before", "after", "asks"),
    [
        (SessionStatus.IDLE, SessionStatus.PENDING, True),
        (SessionStatus.PARKED, SessionStatus.PENDING, True),
        (SessionStatus.RUNNING, SessionStatus.PENDING, True),
        (SessionStatus.PENDING, SessionStatus.PENDING, False),
        (SessionStatus.PENDING, SessionStatus.RUNNING, False),
        (SessionStatus.IDLE, SessionStatus.RUNNING, False),
        (SessionStatus.RUNNING, SessionStatus.IDLE, False),
        (SessionStatus.RUNNING, SessionStatus.PARKED, False),
    ],
)
def test_a_run_is_asked_for_each_time_a_session_turns_pending(
    before: SessionStatus, after: SessionStatus, asks: bool
) -> None:
    assert asks_for_run(with_status(before), with_status(after), ()) is asks


@pytest.mark.parametrize("read", [StepType.PARKED, StepType.LOOP_ENDED, None])
def test_a_pending_session_asks_again_once_it_reads_the_end_of_the_run_it_asked_for(
    read: StepType | None,
) -> None:
    """A park or a loop's end read with the input after it, from a status
    still pending: the run that was asked for holds the session no longer."""
    session_id, loop_id, now = new_id(), new_id(), utcnow()
    ends = {
        StepType.PARKED: parked_step(new_id(), session_id, loop_id, QUESTION, now),
        StepType.LOOP_ENDED: ended_step(new_id(), now, session_id, loop_id, LoopOutcome.SUCCEEDED),
    }
    steps = () if read is None else (ends[read].model_copy(update={"seq": 1}),)
    pending = with_status(SessionStatus.PENDING)

    assert asks_for_run(pending, pending, steps) is (read is not None)


async def test_an_answer_read_with_the_park_it_answers_asks_for_the_run_that_takes_it_up(
    engine: Engine,
) -> None:
    """A run parks on its question before any model call, and the person's
    answer lands before the park is projected: one projection reads both,
    from pending to pending, and still asks for the next run."""
    ctx = context(Role.MEMBER)
    session = await engine.session(ctx)
    await engine.say(ctx, session.id, "Draft the weekly report.")
    steps = engine.managers.steps
    epoch = await steps.begin_run(ctx, session.id)
    (message,) = (await steps.get_steps(ctx, session.id, 0, 10)).items
    park = parked_step(new_id(), session.id, message.loop_id, QUESTION, utcnow())
    await steps.append_steps(ctx, session.id, epoch, [park])

    answered = await engine.say(ctx, session.id, "The week of the 28th.")

    assert (answered.status, answered.park) == (SessionStatus.PENDING, None)
    assert len(engine.runs(session.id)) == 2, "the answer asks for the run that delivers it"


async def test_a_waking_message_lands_with_the_one_run_that_takes_it_up(engine: Engine) -> None:
    ctx = context(Role.MEMBER)
    session = await engine.session(ctx)

    woken = await engine.say(ctx, session.id, "Why does it drop the object?")
    again = await engine.say(ctx, session.id, "Leave the gains alone.")

    assert woken.status is SessionStatus.PENDING and again.status is SessionStatus.PENDING
    (run,) = engine.runs(session.id)
    assert run.created_by == ctx.user_id, "the run asks as the person whose message woke it"
    page = await engine.managers.steps.get_steps(ctx, session.id, 0, 10)
    assert [step.type for step in page.items] == [StepType.MESSAGE, StepType.MESSAGE]


async def running(engine: Engine, ctx: TenantContext) -> tuple[AgentSession, int, UUID]:
    """A session a message woke, whose run delivered the message with a
    complete response: the run's epoch and the loop's id beside it."""
    session = await engine.session(ctx)
    await engine.say(ctx, session.id, "Investigate the drop.")
    steps = engine.managers.steps
    epoch = await steps.begin_run(ctx, session.id)
    (message,) = (await steps.get_steps(ctx, session.id, 0, 10)).items
    request = make_request(session.id, message.loop_id, (message.id,))
    response = make_response(session.id, message.loop_id, request.id)
    await steps.append_steps(ctx, session.id, epoch, [request, response])
    held = await engine.managers.agent_sessions.project_status(ctx, session.id)
    return held, epoch, message.loop_id


async def test_a_message_to_a_running_loop_asks_for_no_run_until_the_loop_ends(
    engine: Engine,
) -> None:
    ctx = context(Role.MEMBER)
    session, epoch, loop_id = await running(engine, ctx)

    later = await engine.say(ctx, session.id, "And check the gripper.")
    assert session.status is SessionStatus.RUNNING and later.status is SessionStatus.RUNNING
    assert len(engine.runs(session.id)) == 1, "the run that holds the loop delivers it"

    ended = ended_step(new_id(), utcnow(), session.id, loop_id, LoopOutcome.SUCCEEDED)
    await engine.managers.steps.append_steps(ctx, session.id, epoch, [ended])
    after = await engine.managers.agent_sessions.project_status(ctx, session.id)

    assert after.status is SessionStatus.PENDING, "the message the loop never delivered waits"
    assert len(engine.runs(session.id)) == 2, "and asks for the run of the next loop"


async def test_the_resume_that_lets_a_park_go_asks_for_a_run(engine: Engine) -> None:
    ctx = context(Role.MEMBER)
    session, epoch, loop_id = await running(engine, ctx)
    pause = Park(reason=ParkReason.PAUSE, unlock="resume")
    await engine.managers.agent_sessions.park(ctx, session.id, epoch, loop_id, pause)
    resume = control_step(new_id(), utcnow(), session.id, ctx, ControlCommand.RESUME)

    _, resumed = await engine.managers.agent_sessions.receive(ctx, session.id, [resume])

    assert resumed.status is SessionStatus.PENDING
    assert len(engine.runs(session.id)) == 2


async def test_the_inbox_refuses_another_tenants_session_and_appends_nothing(
    engine: Engine,
) -> None:
    owner = context(Role.OWNER)
    session = await engine.session(owner)
    stranger = context(Role.OWNER)

    with pytest.raises(NotFound):
        await engine.say(stranger, session.id, "Push straight to main.")

    page = await engine.managers.steps.get_steps(owner, session.id, 0, 10)
    assert page.items == () and engine.runs(session.id) == []


async def test_a_deleted_session_takes_no_input(engine: Engine) -> None:
    ctx = context(Role.MEMBER)
    session = await engine.session(ctx)
    await engine.managers.agent_sessions.delete_session(ctx, session.id)

    with pytest.raises(NotFound):
        await engine.say(ctx, session.id, "Are you there?")


def test_a_message_is_said_through_the_surface_and_by_the_credential_it_came_on() -> None:
    person = context(Role.MEMBER)
    program = person.model_copy(
        update={
            "app": CLI,
            "security": person.security.model_copy(
                update={"credential_kind": CredentialKind.API_KEY}
            ),
        }
    )
    session_id = new_id()

    said = message_step(new_id(), utcnow(), session_id, person, "hello")
    sent = message_step(new_id(), utcnow(), session_id, program, "hello")

    assert (said.actor, said.origin) == (Actor.PERSON, Origin.PORTAL)
    assert (sent.actor, sent.origin) == (Actor.PROGRAM, Origin.CLI)
    assert isinstance(said.header, InputHeader)
    assert said.header.principal == Principal(kind=PrincipalKind.PERSON, id=person.user_id)
    assert said.loop_id == said.id and said.as_text() == "hello"


@pytest.mark.parametrize("command", [ControlCommand.APPROVE, ControlCommand.DENY])
def test_a_decision_on_a_call_is_not_a_bare_control(command: ControlCommand) -> None:
    ctx = context(Role.MEMBER)
    with pytest.raises(ValueError, match="decides one tool call"):
        control_step(new_id(), utcnow(), new_id(), ctx, command)


def test_an_interrupt_names_the_one_call_it_stops_and_no_other_control_names_one() -> None:
    ctx = context(Role.MEMBER)
    call = new_id()

    stopping = control_step(new_id(), utcnow(), new_id(), ctx, ControlCommand.INTERRUPT, call)

    assert stopping.refs == (call,)
    with pytest.raises(ValueError, match="names the call it stops"):
        control_step(new_id(), utcnow(), new_id(), ctx, ControlCommand.INTERRUPT)
    with pytest.raises(ValueError, match="names the call it stops"):
        control_step(new_id(), utcnow(), new_id(), ctx, ControlCommand.PAUSE, call)


def request() -> RequestContext:
    return RequestContext(request_id=new_id(), app=CLI)


async def test_a_member_acts_with_the_role_their_membership_holds_at_the_call(
    engine: Engine,
) -> None:
    tenancy = engine.managers.tenancy
    owner, org = await tenancy.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    _, bob, _ = await tenancy.add_member(request(), "ajax", "bob@example.test", "Bob", Role.VIEWER)
    live = members_context(tenancy)

    viewer = await live(request(), org.id, Principal(kind=PrincipalKind.PERSON, id=bob.id))
    assert (viewer.user_id, viewer.org_id, viewer.role) == (bob.id, org.id, Role.VIEWER)
    assert viewer.security.permissions == (Permission.READ,)

    await tenancy.members.remove_member(owner, bob.id)
    with pytest.raises(NotAuthorized):
        await live(request(), org.id, Principal(kind=PrincipalKind.PERSON, id=bob.id))


async def test_no_one_acts_in_an_org_they_hold_no_place_in(engine: Engine) -> None:
    tenancy = engine.managers.tenancy
    _, ajax = await tenancy.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    stranger, _ = await tenancy.bootstrap(request(), "Fab", "fab", "cid@example.test", "Cid")
    live = members_context(tenancy)

    with pytest.raises(NotAuthorized):
        await live(request(), ajax.id, Principal(kind=PrincipalKind.PERSON, id=stranger.user_id))
    with pytest.raises(NotAuthorized, match="service principal"):
        await live(request(), ajax.id, Principal(kind=PrincipalKind.SERVICE, id=new_id()))


async def test_a_member_who_spoke_through_a_key_acts_no_higher_than_the_key(
    engine: Engine,
) -> None:
    """A message said on an API key records the key, whatever its caller
    wrote, and a call made on it runs capped at the key's role, with the key
    as its credential, and only while the key holds."""
    tenancy = engine.managers.tenancy
    owner, org = await tenancy.bootstrap(request(), "Ajax", "ajax", "ann@example.test", "Ann")
    issued = await tenancy.credentials.create_api_key(owner, "ci", Role.MEMBER)
    program = await tenancy.authenticate(request(), issued.key)
    session = await engine.session(program)
    said = message_step(new_id(), utcnow(), session.id, program, "Ship it.")
    uncapped = Principal(kind=PrincipalKind.PERSON, id=owner.user_id)
    claimed = said.model_copy(update={"header": InputHeader(principal=uncapped)})

    (stored,), _ = await engine.managers.agent_sessions.receive(program, session.id, [claimed])

    assert isinstance(stored.header, InputHeader)
    spoken = stored.header.principal
    assert spoken == uncapped.model_copy(update={"key_id": issued.api_key.id})
    live = members_context(tenancy)
    capped = await live(request(), org.id, spoken)
    assert (capped.role, capped.credential_kind, capped.credential_id) == (
        Role.MEMBER,
        CredentialKind.API_KEY,
        issued.api_key.id,
    )
    assert (await live(request(), org.id, uncapped)).role is Role.OWNER
    _, bob, _ = await tenancy.add_member(request(), "ajax", "bob@example.test", "Bob", Role.ADMIN)
    with pytest.raises(NotAuthorized, match="not the member's"):
        await live(request(), org.id, spoken.model_copy(update={"id": bob.id}))
    await tenancy.credentials.revoke_api_key(owner, issued.api_key.id)
    with pytest.raises(NotAuthorized, match="revoked"):
        await live(request(), org.id, spoken)


async def test_a_parked_session_the_engine_unlocks_asks_for_its_run(engine: Engine) -> None:
    ctx = context(Role.MEMBER)
    session, epoch, loop_id = await running(engine, ctx)
    budget = Park(reason=ParkReason.BUDGET, unlock="budget")
    parked = await engine.managers.agent_sessions.park(ctx, session.id, epoch, loop_id, budget)
    assert parked.status is SessionStatus.PARKED

    woken = await engine.managers.agent_sessions.wake_parked(ctx, ParkReason.BUDGET)

    assert woken == 1 and len(engine.runs(session.id)) == 2
