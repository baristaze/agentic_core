"""The session on its own: its status as a projection of its steps, and the
agent sessions manager over the memory storage, with the history it reads
appended through the steps manager."""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from contracts.agent_session_storage import make_session
from contracts.doubles import context
from contracts.factories import make_org
from contracts.step_storage import (
    a_person,
    make_event,
    make_message,
    make_parked,
    make_request,
    make_response,
)
from contracts.tools import stand_ins

from acme.infra.impl.local import InfraLocalImpl
from acme.om.agent_sessions.impl.manager import AgentSessionsManagerImpl, AgentSessionsOptions
from acme.om.agent_sessions.rules import Projection, after_step, announces, projected
from acme.om.agent_sessions.storage import AgentSessionStorageInterface
from acme.om.agent_sessions.storage.impl.memory import AgentSessionStorageMemoryImpl
from acme.om.agent_sessions.types.agent_session import AgentSession, SessionStatus
from acme.om.base import new_id, utcnow
from acme.om.context import AppContext, AppType, RequestContext, Role, TenantContext
from acme.om.exceptions import NotAuthorized, NotFound, PreconditionFailed, ValidationFailed
from acme.om.outbox.storage.impl.memory import OutboxStorageMemoryImpl
from acme.om.outbox.types.row import OutboxRow
from acme.om.root import Managers, build_managers
from acme.om.steps.impl.manager import StepsManagerImpl, StepsOptions, no_registry
from acme.om.steps.types.header import (
    ControlCommand,
    ControlHeader,
    DecidedCall,
    InputHeader,
    LoopEndedHeader,
    LoopOutcome,
    MarkHeader,
    ModelResponseHeader,
    Park,
    ParkedHeader,
    ParkReason,
)
from acme.om.steps.types.page import StepCursor
from acme.om.steps.types.step import Actor, Origin, Step, StepType
from acme.om.storage.impl.memory import StorageMemoryImpl

# What `make_session` names, so the agents manager classes every tool it
# offers.
TOOLS = stand_ins("read_log", "run_tests")


SESSION = new_id()
LOOP = new_id()
APP = AppContext(type=AppType.PORTAL, version="portal@test")


def a_step(step_type: StepType, header: Any, seq: int = 0) -> Step:
    return Step(
        id=new_id(),
        created_at=utcnow(),
        session_id=SESSION,
        seq=seq,
        loop_id=LOOP,
        type=step_type,
        actor=Actor.ENGINE,
        origin=Origin.ENGINE,
        header=header,
    )


def control(command: ControlCommand) -> Step:
    """A control step; an approve or a deny names the call it decides."""
    call = None
    if command in (ControlCommand.APPROVE, ControlCommand.DENY):
        expires = utcnow() + timedelta(hours=1) if command is ControlCommand.APPROVE else None
        call = DecidedCall(
            tool="t", input_hash="h", decided_by=new_id(), role=Role.OWNER, expires_at=expires
        )
    return a_step(StepType.CONTROL, ControlHeader(command=command, call=call))


def parked_for(reason: ParkReason) -> Step:
    return a_step(StepType.PARKED, ParkedHeader(park=Park(reason=reason, unlock="x")))


IDLE = Projection(SessionStatus.IDLE, None, archived=False)
PENDING = Projection(SessionStatus.PENDING, None, archived=False)
RUNNING = Projection(SessionStatus.RUNNING, None, archived=False)


def parked_state(reason: ParkReason) -> Projection:
    return Projection(SessionStatus.PARKED, Park(reason=reason, unlock="x"), archived=False)


# The projection, one step at a time.


def test_an_input_that_wakes_starts_a_loop_on_an_idle_session_and_no_other() -> None:
    message = make_message(SESSION)
    quiet = a_step(StepType.EVENT, InputHeader(waking=False, principal=a_person()))
    woken = after_step(IDLE, message)
    assert (woken.status, woken.pending_input) == (SessionStatus.PENDING, message.id)
    assert after_step(IDLE, quiet) == IDLE
    assert after_step(RUNNING, message).status is SessionStatus.RUNNING
    assert after_step(parked_state(ParkReason.BUDGET), message).status is SessionStatus.PARKED


def test_an_event_built_with_no_word_on_waking_leaves_an_idle_session_idle() -> None:
    """A principal's message wakes by default, and an event from outside
    does not."""
    event = make_event(SESSION)
    assert isinstance(event.header, InputHeader) and event.header.waking is False
    assert after_step(IDLE, event) == IDLE
    unsaid = InputHeader(principal=a_person())
    assert after_step(IDLE, a_step(StepType.EVENT, unsaid)) == IDLE
    assert after_step(IDLE, a_step(StepType.MESSAGE, unsaid)).status is (SessionStatus.PENDING)


def fold(steps: list[Step]) -> Projection:
    state = IDLE
    for step in steps:
        state = after_step(state, step)
    return state


def response_to(request: Step, *, truncated: bool = False, abandoned: bool = False) -> Step:
    header = ModelResponseHeader(truncated=truncated, abandoned=abandoned)
    return make_response(SESSION, LOOP, request.id).model_copy(update={"header": header})


def loop_ended() -> Step:
    return a_step(StepType.LOOP_ENDED, LoopEndedHeader(outcome=LoopOutcome.SUCCEEDED))


def test_a_loop_that_ends_with_a_waking_input_undelivered_leaves_the_session_pending() -> None:
    """An input stays pending until a model request that references it has a
    complete response. One that arrives after the last request is still
    waiting when the loop ends, so the session is pending, not idle."""
    first = make_message(SESSION)
    request = make_request(SESSION, first.id, (first.id,))
    answered = response_to(request)
    late = make_message(SESSION, "and the monthly one?")
    assert fold([first, request, answered, loop_ended()]).status is SessionStatus.IDLE
    waiting = fold([first, request, answered, late, loop_ended()])
    assert (waiting.status, waiting.pending_input) == (SessionStatus.PENDING, late.id)
    carried = make_request(SESSION, first.id, (late.id,))
    delivered = fold([first, request, answered, late, carried, response_to(carried), loop_ended()])
    assert (delivered.status, delivered.pending_input) == (SessionStatus.IDLE, None)
    other = make_request(SESSION, first.id, (first.id,))
    passed_by = fold([first, request, answered, late, other, response_to(other), loop_ended()])
    assert passed_by.status is SessionStatus.PENDING, "a request that does not carry it"
    for cut_short in (response_to(request, truncated=True), response_to(request, abandoned=True)):
        cut = fold([first, request, cut_short, loop_ended()])
        assert cut.status is SessionStatus.PENDING, cut_short.header
    quiet = fold([first, request, answered, make_event(SESSION), loop_ended()])
    assert quiet.status is SessionStatus.IDLE, "an input that does not wake waits quietly"


def test_a_run_holds_the_loop_until_it_parks_or_ends() -> None:
    request = make_request(SESSION, LOOP)
    assert after_step(PENDING, request) == RUNNING
    assert after_step(IDLE, request) == IDLE, "no run writes to a session with no loop open"
    held = after_step(RUNNING, parked_for(ParkReason.JOB))
    assert held == parked_state(ParkReason.JOB)
    assert after_step(held, request) == held, "a parked loop stays parked until it resumes"
    assert after_step(held, a_step(StepType.RESUMED, MarkHeader())) == RUNNING
    ended = a_step(StepType.LOOP_ENDED, LoopEndedHeader(outcome=LoopOutcome.SUCCEEDED))
    assert after_step(RUNNING, ended) == IDLE


@pytest.mark.parametrize(
    ("command", "cleared"),
    [
        (ControlCommand.UNLOCK, set(ParkReason)),
        (ControlCommand.CANCEL, set(ParkReason)),
        (ControlCommand.RESUME, {ParkReason.PAUSE}),
        (ControlCommand.APPROVE, {ParkReason.PERSON}),
        (ControlCommand.DENY, {ParkReason.PERSON}),
        (ControlCommand.PAUSE, set()),
        (ControlCommand.INTERRUPT, set()),
        (ControlCommand.COMPACT, set()),
    ],
)
def test_a_control_clears_the_parks_it_names_and_no_other(
    command: ControlCommand, cleared: set[ParkReason]
) -> None:
    for reason in ParkReason:
        after = after_step(parked_state(reason), control(command))
        expected = PENDING if reason in cleared else parked_state(reason)
        assert after == expected, (command, reason)
    assert after_step(IDLE, control(command)) == IDLE


def test_an_archived_session_records_events_and_a_message_unarchives_it() -> None:
    archived = Projection(SessionStatus.IDLE, None, archived=True)
    event = a_step(StepType.EVENT, InputHeader(waking=True, principal=a_person()))
    assert after_step(archived, event) == archived
    back = after_step(archived, make_message(SESSION))
    assert (back.status, back.archived) == (SessionStatus.PENDING, False)


# The projection over a history.


def a_history() -> list[Step]:
    message = make_message(SESSION)
    request = make_request(SESSION, message.id, (message.id,))
    response = make_response(SESSION, message.id, request.id)
    steps = [message, request, response, make_parked(SESSION, message.id)]
    steps += [
        control(ControlCommand.APPROVE),
        a_step(StepType.RESUMED, MarkHeader()),
        a_step(StepType.LOOP_ENDED, LoopEndedHeader(outcome=LoopOutcome.SUCCEEDED)),
        make_message(SESSION, "and the monthly one?"),
    ]
    return [step.model_copy(update={"seq": n}) for n, step in enumerate(steps, start=1)]


def test_the_status_is_rebuilt_from_the_steps_whatever_the_batches() -> None:
    session = make_session()
    history = a_history()
    whole = projected(session, history, utcnow(), session.created_by)
    by_one = session
    for step in history:
        by_one = projected(by_one, [step], utcnow(), session.created_by)
    assert (whole.status, whole.park, whole.status_seq) == (SessionStatus.PENDING, None, 8)
    assert (by_one.status, by_one.park, by_one.status_seq) == (whole.status, whole.park, 8)
    assert whole.version == session.version + 1


def test_a_step_read_already_changes_nothing() -> None:
    session = make_session()
    history = a_history()
    caught_up = projected(session, history[:4], utcnow(), session.created_by)
    assert caught_up.status is SessionStatus.PARKED and caught_up.status_seq == 4
    assert projected(caught_up, history[:4], utcnow(), session.created_by) is caught_up
    assert projected(caught_up, [], utcnow(), session.created_by) is caught_up


def test_a_change_of_status_or_the_end_of_a_loop_is_announced_and_no_step_alone() -> None:
    session = make_session()
    history = a_history()
    woken = projected(session, history[:1], utcnow(), session.created_by)
    assert announces(session, woken, history[:1])
    running = projected(woken, history[1:2], utcnow(), session.created_by)
    still = projected(running, history[2:3], utcnow(), session.created_by)
    assert announces(woken, running, history[1:2])
    assert not announces(running, still, history[2:3])
    # A loop that ends and a new one that begins within one read leave the
    # status where it was, and the end is still announced.
    resumed = projected(still, history[3:6], utcnow(), session.created_by)
    assert resumed.status is SessionStatus.RUNNING
    again = projected(resumed, history[6:], utcnow(), session.created_by)
    assert again.status is SessionStatus.PENDING
    assert announces(resumed, again, history[6:])


def test_a_session_carries_a_park_exactly_while_parked() -> None:
    session = make_session()
    with pytest.raises(ValueError, match="exactly while it is parked"):
        AgentSession.model_validate({**session.model_dump(), "status": SessionStatus.PARKED})
    with pytest.raises(ValueError, match="exactly while it is parked"):
        AgentSession.model_validate(
            {**session.model_dump(), "park": {"reason": "budget", "unlock": "raise"}}
        )


# The manager.


@pytest.fixture
def managers(tmp_path: Path) -> Managers:
    return build_managers(StorageMemoryImpl(), InfraLocalImpl(tmp_path), tool_catalog=TOOLS)


async def kinds(managers: Managers, ctx: TenantContext, target: UUID) -> list[str]:
    """What the tenant's stream announced of one session."""
    events = await managers.events.get_events(ctx, 0, 100)
    return [e.kind.rsplit(".", 1)[-1] for e in events if e.target_id == target]


async def test_a_session_lands_idle_at_its_own_root_and_a_child_joins_its_tree(
    managers: Managers,
) -> None:
    ctx = context(Role.MEMBER)
    sent = make_session().model_copy(update={"status_seq": 9, "version": 4, "root_id": new_id()})
    root = await managers.agent_sessions.create_session(ctx, sent)
    assert (root.status, root.status_seq, root.version) == (SessionStatus.IDLE, 0, 1)
    assert root.root_id == root.id and root.created_by == ctx.user_id
    child = await managers.agent_sessions.create_session(ctx, make_session(parent=root))
    assert (child.parent_id, child.root_id) == (root.id, root.id)
    grandchild = await managers.agent_sessions.create_session(ctx, make_session(parent=child))
    assert grandchild.root_id == root.id
    assert await managers.agent_sessions.create_session(ctx, root) == root
    assert await kinds(managers, ctx, root.id) == ["created"]
    orphan = make_session().model_copy(update={"parent_id": new_id()})
    with pytest.raises(ValidationFailed):
        await managers.agent_sessions.create_session(ctx, orphan)


async def test_another_tenant_finds_no_session(managers: Managers) -> None:
    ctx = context(Role.MEMBER)
    session = await managers.agent_sessions.create_session(ctx, make_session())
    other = context(Role.OWNER)
    with pytest.raises(NotFound):
        await managers.agent_sessions.get_session(other, session.id)
    with pytest.raises(ValidationFailed):
        await managers.agent_sessions.create_session(other, make_session(parent=session))
    assert (await managers.agent_sessions.get_sessions(other, None, None, 10)).items == ()


async def test_sessions_page_by_status(managers: Managers) -> None:
    ctx = context(Role.MEMBER)
    made = [await managers.agent_sessions.create_session(ctx, make_session()) for _ in range(3)]
    page = await managers.agent_sessions.get_sessions(ctx, SessionStatus.IDLE, None, 2)
    assert page.items == tuple(made[:2]) and page.has_more
    rest = await managers.agent_sessions.get_sessions(ctx, None, made[1].id, 10)
    assert rest.items == (made[2],) and not rest.has_more
    assert (
        await managers.agent_sessions.get_sessions(ctx, SessionStatus.RUNNING, None, 10)
    ).items == ()


async def test_the_status_follows_the_history_and_announces_each_change(
    managers: Managers,
) -> None:
    ctx = context(Role.MEMBER)
    session = await managers.agent_sessions.create_session(ctx, make_session())
    sid = session.id
    steps, sessions = managers.steps, managers.agent_sessions
    (message,) = await steps.append_inputs(ctx, sid, [make_message(sid)])
    woken = await sessions.project_status(ctx, sid)
    assert (woken.status, woken.status_seq) == (SessionStatus.PENDING, 1)
    epoch = await steps.begin_run(ctx, sid)
    request = make_request(sid, message.id, (message.id,))
    await steps.append_steps(ctx, sid, epoch, [request, make_parked(sid, message.id)])
    held = await sessions.project_status(ctx, sid)
    assert held.status is SessionStatus.PARKED and held.park is not None
    assert held.park.reason is ParkReason.PERSON and held.status_seq == 3
    assert await sessions.project_status(ctx, sid) == held, "nothing new, nothing written"
    assert await kinds(managers, ctx, sid) == ["created", "updated", "updated"]
    assert (await sessions.get_sessions(ctx, SessionStatus.PARKED, None, 10)).items == (held,)


async def test_an_input_still_undelivered_when_a_loop_ends_is_kept_across_projections(
    managers: Managers,
) -> None:
    ctx = context(Role.MEMBER)
    sid = (await managers.agent_sessions.create_session(ctx, make_session())).id
    steps, sessions = managers.steps, managers.agent_sessions
    (first,) = await steps.append_inputs(ctx, sid, [make_message(sid)])
    epoch = await steps.begin_run(ctx, sid)
    request = make_request(sid, first.id, (first.id,))
    await steps.append_steps(ctx, sid, epoch, [request, make_response(sid, first.id, request.id)])
    (late,) = await steps.append_inputs(ctx, sid, [make_message(sid, "and the monthly one?")])
    running = await sessions.project_status(ctx, sid)
    assert (running.status, running.pending_input) == (SessionStatus.RUNNING, late.id)
    ended = Step.model_validate(
        {
            **loop_ended().model_dump(),
            "session_id": sid,
            "loop_id": first.id,
        }
    )
    await steps.append_steps(ctx, sid, epoch, [ended])
    after = await sessions.project_status(ctx, sid)
    assert (after.status, after.pending_input) == (SessionStatus.PENDING, late.id)


async def test_the_sweep_purges_no_living_tenants_sessions_or_history(managers: Managers) -> None:
    owner, _ = await managers.tenancy.bootstrap(
        RequestContext(request_id=new_id(), app=APP),
        "Ajax",
        f"ajax-{new_id().hex[-8:]}",
        f"a-{new_id().hex[-8:]}@x.test",
        "Ann",
    )
    session = await managers.agent_sessions.create_session(owner, make_session())
    await managers.steps.append_inputs(owner, session.id, [make_message(session.id)])
    assert await managers.agent_sessions.purge_tenant(owner) == 0
    assert await managers.steps.purge_tenant(owner) == 0
    assert await managers.agent_sessions.get_session(owner, session.id) == session


async def test_a_long_history_is_folded_a_batch_at_a_time(managers: Managers) -> None:
    ctx = context(Role.MEMBER)
    sid = (await managers.agent_sessions.create_session(ctx, make_session())).id
    for _ in range(5):
        await managers.steps.append_inputs(ctx, sid, [make_message(sid) for _ in range(100)])
    projected_session = await managers.agent_sessions.project_status(ctx, sid)
    assert projected_session.status_seq == 500
    assert projected_session.status is SessionStatus.PENDING


async def nothing_held(org_id: UUID, session_id: UUID, tree_id: UUID | None) -> None:
    """A session that no other namespace holds anything of."""


class Overtaken(AgentSessionStorageMemoryImpl):
    """A session storage another writer reaches first, `jumps` times: it
    moves the stored version just before each of those writes lands."""

    jumps = 0

    async def write_session(
        self,
        org_id: UUID,
        session: AgentSession,
        expected_version: int,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> None:
        if self.jumps:
            self.jumps -= 1
            current = await self.read_session(org_id, session.id)
            if current is not None:
                moved = current.model_copy(update={"version": current.version + 1})
                await super().write_session(org_id, moved, current.version, ())
        await super().write_session(org_id, session, expected_version, outbox_rows)


async def test_a_projection_behind_another_writer_reads_again_and_folds_on(
    tmp_path: Path,
) -> None:
    """A writer that lands between the projection's read and its write moves
    the version; the projection reads what it left and folds on from it, and
    gives up after its attempts, landing nothing."""
    storage = StorageMemoryImpl()
    managers = build_managers(storage, InfraLocalImpl(tmp_path), tool_catalog=TOOLS)
    outbox = storage.get_outbox_storage()
    assert isinstance(outbox, OutboxStorageMemoryImpl)
    overtaken = Overtaken(outbox)
    sessions = AgentSessionsManagerImpl(
        overtaken,
        managers.steps,
        managers.tenancy,
        managers.outbox,
        AgentSessionsOptions(project_attempts=2),
        purged=nothing_held,
    )
    ctx = context(Role.MEMBER)
    created = await sessions.create_session(ctx, make_session())
    await managers.steps.append_inputs(ctx, created.id, [make_message(created.id)])
    overtaken.jumps = 1
    after = await sessions.project_status(ctx, created.id)
    assert (after.status, after.status_seq, after.version) == (SessionStatus.PENDING, 1, 3)
    await managers.steps.append_inputs(ctx, created.id, [make_message(created.id)])
    overtaken.jumps = 2
    with pytest.raises(PreconditionFailed):
        await sessions.project_status(ctx, created.id)
    assert (await sessions.get_session(ctx, created.id)).status_seq == 1


async def test_a_deleted_tenants_purge_takes_up_at_most_a_batch_of_sessions_a_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each session the purge of a tenant takes up brings its workspace and
    its records along, so one call reads the smaller session batch, not the
    rows' batch; the tenant reads as settled only once a call finds none."""
    storage = StorageMemoryImpl()
    managers = build_managers(storage, InfraLocalImpl(tmp_path), tool_catalog=TOOLS)
    taken: list[UUID] = []

    async def held(org_id: UUID, session_id: UUID, tree_id: UUID | None) -> None:
        taken.append(session_id)

    sessions = AgentSessionsManagerImpl(
        storage.get_agent_session_storage(),
        managers.steps,
        managers.tenancy,
        managers.outbox,
        AgentSessionsOptions(purge_batch=1000, purge_sessions=2),
        purged=held,
    )
    ctx = context(Role.MEMBER)
    for _ in range(3):
        await sessions.create_session(ctx, make_session())

    async def expired(asked: TenantContext) -> bool:
        return asked.org_id == ctx.org_id

    monkeypatch.setattr(managers.tenancy, "tenant_expired", expired)
    assert [await sessions.purge_tenant(ctx) for _ in range(3)] == [2, 1, 0]
    assert len(taken) == len(set(taken)) == 3


async def test_only_an_idle_session_is_archived_and_a_message_brings_it_back(
    managers: Managers,
) -> None:
    ctx = context(Role.MEMBER)
    sid = (await managers.agent_sessions.create_session(ctx, make_session())).id
    archived = await managers.agent_sessions.archive_session(ctx, sid)
    assert archived.archived_at is not None and archived.version == 2
    assert await managers.agent_sessions.archive_session(ctx, sid) == archived
    await managers.steps.append_inputs(ctx, sid, [make_event(sid)])
    quiet = await managers.agent_sessions.project_status(ctx, sid)
    assert quiet.archived_at is not None and quiet.status is SessionStatus.IDLE
    await managers.steps.append_inputs(ctx, sid, [make_message(sid)])
    back = await managers.agent_sessions.project_status(ctx, sid)
    assert back.archived_at is None and back.status is SessionStatus.PENDING
    with pytest.raises(ValidationFailed):
        await managers.agent_sessions.archive_session(ctx, sid)


async def test_a_viewer_reads_and_writes_nothing(managers: Managers) -> None:
    org = make_org()
    member, viewer = context(Role.MEMBER, org), context(Role.VIEWER, org)
    session = await managers.agent_sessions.create_session(member, make_session())
    assert await managers.agent_sessions.get_session(viewer, session.id) == session
    with pytest.raises(NotAuthorized):
        await managers.agent_sessions.create_session(viewer, make_session())
    with pytest.raises(NotAuthorized):
        await managers.agent_sessions.project_status(viewer, session.id)
    with pytest.raises(NotAuthorized):
        await managers.agent_sessions.archive_session(viewer, session.id)


async def test_a_deleted_session_is_hidden_from_every_read_and_comes_back_whole(
    managers: Managers,
) -> None:
    """Mark deleted hides a session from every read: by id, on a page, as a
    parent, and from the writes that read it first. Its history stays as it
    was, content and shape, and so does every field of the session. Unmarked,
    it comes back as it was, and the delete and the return are announced."""
    ctx = context(Role.MEMBER)
    sessions, steps = managers.agent_sessions, managers.steps
    sid = (await sessions.create_session(ctx, make_session())).id
    history = await steps.append_inputs(ctx, sid, [make_event(sid), make_event(sid)])
    idle = await sessions.project_status(ctx, sid)
    deleted = await sessions.delete_session(ctx, sid)
    assert deleted.deleted_at is not None and deleted.deleted_by == ctx.user_id
    reads = (
        sessions.get_session,
        sessions.project_status,
        sessions.archive_session,
        sessions.delete_session,
    )
    for read in reads:
        with pytest.raises(NotFound):
            await read(ctx, sid)
    assert (await sessions.get_sessions(ctx, None, None, 10)).items == ()
    with pytest.raises(ValidationFailed):
        await sessions.create_session(ctx, make_session(parent=deleted))
    assert (await steps.get_steps(ctx, sid, 0, 10)).items == history

    restored = await sessions.restore_session(ctx, sid)
    changed = {"version", "updated_at", "updated_by"}
    assert restored.model_dump(exclude=changed) == idle.model_dump(exclude=changed)
    assert restored.version == idle.version + 2
    assert await sessions.get_session(ctx, sid) == restored
    assert (await sessions.get_sessions(ctx, None, None, 10)).items == (restored,)
    assert (await steps.get_steps(ctx, sid, 0, 10)).items == history
    assert await sessions.restore_session(ctx, sid) == restored, "not marked: as it is"
    assert await kinds(managers, ctx, sid) == ["created", "deleted", "updated"]


async def test_only_an_idle_session_is_deleted(managers: Managers) -> None:
    ctx = context(Role.MEMBER)
    sid = (await managers.agent_sessions.create_session(ctx, make_session())).id
    await managers.steps.append_inputs(ctx, sid, [make_message(sid)])
    await managers.agent_sessions.project_status(ctx, sid)
    with pytest.raises(ValidationFailed):
        await managers.agent_sessions.delete_session(ctx, sid)
    with pytest.raises(NotAuthorized):
        await managers.agent_sessions.delete_session(context(Role.VIEWER), sid)


class Clock:
    """A clock a case moves by hand."""

    def __init__(self) -> None:
        self.now = utcnow()

    def __call__(self) -> datetime:
        return self.now


def purging(
    managers: Managers,
    storage: StorageMemoryImpl,
    clock: Clock,
    *,
    sessions: AgentSessionStorageInterface | None = None,
    batch: int = 1000,
) -> AgentSessionsManagerImpl:
    """The agent sessions manager on a clock of the case's, with a 30-day
    retention and a history purged `batch` steps at a time."""
    return AgentSessionsManagerImpl(
        sessions or storage.get_agent_session_storage(),
        StepsManagerImpl(
            storage.get_step_storage(),
            managers.tenancy,
            StepsOptions(purge_batch=batch),
            instructs=no_registry,
        ),
        managers.tenancy,
        managers.outbox,
        AgentSessionsOptions(retention=timedelta(days=30)),
        clock=clock,
        purged=nothing_held,
    )


async def test_a_purge_before_the_retention_ends_is_refused_and_one_after_takes_everything(
    tmp_path: Path,
) -> None:
    """Nothing is purged on demand: a session marked deleted within its
    retention, or never marked, keeps its row and its history whenever the
    purge runs, and can be unmarked. Past the retention, counted from the
    last mark, the purge takes the session, its steps, and its cursor."""
    storage = StorageMemoryImpl()
    managers = build_managers(storage, InfraLocalImpl(tmp_path), tool_catalog=TOOLS)
    clock = Clock()
    sessions = purging(managers, storage, clock)
    ctx = context(Role.MEMBER)
    gone, kept = [await sessions.create_session(ctx, make_session()) for _ in range(2)]
    for sid in (gone.id, kept.id):
        await managers.steps.append_inputs(ctx, sid, [make_message(sid)])
    rows, history = storage.get_agent_session_storage(), storage.get_step_storage()

    await sessions.delete_session(ctx, gone.id)
    clock.now += timedelta(days=29)
    assert await sessions.purge_across_tenants() == 0
    found = await rows.read_session(ctx.org_id, gone.id)
    assert found is not None and found.purge_started_at is None
    assert len(await history.read_steps(ctx.org_id, gone.id, 0, 10)) == 1
    await sessions.restore_session(ctx, gone.id)
    await sessions.delete_session(ctx, gone.id)
    clock.now += timedelta(days=29)
    assert await sessions.purge_across_tenants() == 0, "the retention counts from the last mark"

    clock.now += timedelta(days=2)
    assert await sessions.purge_across_tenants() == 1
    assert await rows.read_session(ctx.org_id, gone.id) is None
    assert await history.read_steps(ctx.org_id, gone.id, 0, 10) == []
    assert await history.read_cursor(ctx.org_id, gone.id) == StepCursor()
    assert await sessions.purge_across_tenants() == 0
    assert (await sessions.get_session(ctx, kept.id)).deleted_at is None
    assert len(await history.read_steps(ctx.org_id, kept.id, 0, 10)) == 1


async def test_a_claimed_session_cannot_come_back_and_its_long_history_goes_in_batches(
    tmp_path: Path,
) -> None:
    """The claim makes the delete final: a session whose history is still
    going answers an unmark as one gone, and each pass takes a batch more of
    its history, then its row once the history is gone."""
    storage = StorageMemoryImpl()
    managers = build_managers(storage, InfraLocalImpl(tmp_path), tool_catalog=TOOLS)
    clock = Clock()
    sessions = purging(managers, storage, clock, batch=2)
    ctx = context(Role.MEMBER)
    sid = (await sessions.create_session(ctx, make_session())).id
    await managers.steps.append_inputs(ctx, sid, [make_event(sid) for _ in range(3)])
    rows, history = storage.get_agent_session_storage(), storage.get_step_storage()
    await sessions.delete_session(ctx, sid)
    clock.now += timedelta(days=31)

    assert await sessions.purge_across_tenants() == 1
    claimed = await rows.read_session(ctx.org_id, sid)
    assert claimed is not None and claimed.purge_started_at == clock.now
    assert len(await history.read_steps(ctx.org_id, sid, 0, 10)) == 1
    with pytest.raises(NotFound):
        await sessions.restore_session(ctx, sid)
    with pytest.raises(NotFound):
        await sessions.get_session(ctx, sid)
    assert await sessions.purge_across_tenants() == 1, "the last step and the cursor"
    assert await rows.read_session(ctx.org_id, sid) is not None
    assert await sessions.purge_across_tenants() == 1, "nothing left of the history"
    assert await rows.read_session(ctx.org_id, sid) is None
    assert await sessions.purge_across_tenants() == 0


class RestoredMeanwhile(AgentSessionStorageMemoryImpl):
    """The memory storage, but a person unmarks every session the sweep's
    read finds, between that read and the claim."""

    async def read_purgeable(
        self, deleted_before: datetime, limit: int
    ) -> list[tuple[UUID, AgentSession]]:
        found = await super().read_purgeable(deleted_before, limit)
        for org_id, session in found:
            back = session.model_copy(
                update={"deleted_at": None, "deleted_by": None, "version": session.version + 1}
            )
            await self.write_session(org_id, back, session.version, ())
        return found


async def test_an_unmark_that_lands_before_the_claim_keeps_the_session(tmp_path: Path) -> None:
    storage = StorageMemoryImpl()
    managers = build_managers(storage, InfraLocalImpl(tmp_path), tool_catalog=TOOLS)
    outbox = storage.get_outbox_storage()
    assert isinstance(outbox, OutboxStorageMemoryImpl)
    clock = Clock()
    rows = RestoredMeanwhile(outbox)
    sessions = purging(managers, storage, clock, sessions=rows)
    ctx = context(Role.MEMBER)
    sid = (await sessions.create_session(ctx, make_session())).id
    (step,) = await managers.steps.append_inputs(ctx, sid, [make_message(sid)])
    await sessions.delete_session(ctx, sid)
    clock.now += timedelta(days=31)
    assert await sessions.purge_across_tenants() == 1, "taken up, and found unmarked"
    back = await sessions.get_session(ctx, sid)
    assert back.deleted_at is None and back.purge_started_at is None
    assert (await managers.steps.get_steps(ctx, sid, 0, 10)).items == (step,)
