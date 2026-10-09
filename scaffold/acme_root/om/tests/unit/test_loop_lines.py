"""A session in line for a leased resource, over the memory storage and the
scripted provider: a tool's ask answers at once with its place, and when
the turn ends while it waits, the loop parks on `resource` naming its
place, and makes no model call until the grant, which reaches the model as
the engine's notice with the lease and its token; a grant that lands after
the ask was read and before the park wakes it all the same; a session
cancelled in line leaves it, and the next request is granted; a lease
revoked after its loop ended is told in the next loop, and one that ended
before its session resumed is told as ended, never as granted; and what
an ask's standing owes the model is told once, a grant its call answered
with never."""

from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from contracts.loops import ALLOWED, Loop, call, loop_over, reply, said

from acme.om.agent_sessions.types.agent_session import AgentSession, SessionStatus
from acme.om.agents import line_rules as lines
from acme.om.agents.types.kind import AgentKind, DoneRule, TreeLimits
from acme.om.agents.types.line import InLine
from acme.om.agents.types.run import RunEnd
from acme.om.attribution.types.authority import AuthorityMode
from acme.om.base import Platform, new_id, utcnow
from acme.om.context import TenantContext
from acme.om.leases import LeasesManagerInterface
from acme.om.leases.types.lease import Lease, LeaseStatus
from acme.om.leases.types.request import (
    EndReason,
    LeaseRequest,
    RequestStatus,
    Standing,
    WaiterKind,
)
from acme.om.leases.types.resource import Resource, ResourceKind
from acme.om.steps.types.header import (
    ControlCommand,
    ControlHeader,
    LoopOutcome,
    Park,
    ParkReason,
)
from acme.om.steps.types.step import Actor, Origin, Step, StepType
from acme.om.tools.tool import ToolInterface, ToolRuntime
from acme.om.tools.types.policy import Target
from acme.om.tools.types.tool import Effect, ToolClass, ToolInput, ToolSpec
from acme.om.work.types.work_item import LeaseNoticePayload, WorkKind


class Reserving(ToolInput):
    """No input: the tool asks for the one resource it was given."""


class Reserve(ToolInterface):
    """A product's tool that asks in line for one resource of the `noop`
    kind, as its session's waiter, and answers with its standing."""

    def __init__(self) -> None:
        self._spec = ToolSpec(
            name="reserve",
            description="Ask for the resource.",
            input_model=Reserving,
            output_model=InLine,
            timeout=timedelta(minutes=1),
            authorization_class=ToolClass.WRITE,
            effect=Effect.IDEMPOTENT,
            interruptible=True,
        )
        self.leases: LeasesManagerInterface | None = None
        self.resource_id: UUID | None = None

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
        assert self.leases is not None and self.resource_id is not None
        ask = lines.in_line(an_ask(self.resource_id), runtime.session_id, runtime.key)
        return InLine.of(await self.leases.ask(ctx, ask))


RESERVER = AgentKind(
    name="reserver",
    version=1,
    tools=("lookup", "reserve"),
    done_rule=DoneRule.ANSWER,
    authority=AuthorityMode.DELEGATED,
    tree=TreeLimits(height=2, count=4),
    prompts=("You take the resource the person asks for, and say when you hold it.",),
    policy=ALLOWED,
)


def an_ask(resource_id: UUID) -> LeaseRequest:
    now = utcnow()
    return LeaseRequest(
        id=new_id(),
        created_at=now,
        updated_at=now,
        created_by=new_id(),
        updated_by=new_id(),
        idempotency_key=new_id(),
        kind=ResourceKind.NOOP,
        resource_id=resource_id,
    )


async def held_resource(tmp_path: Path) -> tuple[Loop, Resource, Lease]:
    """A loop whose `reserver` kind asks in line for a resource the owner
    holds."""
    reserve = Reserve()
    loop = loop_over(tmp_path, kinds=(RESERVER,), extra=(reserve,))
    now = utcnow()
    resource = await loop.managers.leases.register(
        loop.owner,
        Resource(
            id=new_id(),
            created_at=now,
            updated_at=now,
            created_by=loop.owner.user_id,
            updated_by=loop.owner.user_id,
            kind=ResourceKind.NOOP,
            ref_id=new_id(),
            max_term_seconds=300,
        ),
    )
    reserve.leases, reserve.resource_id = loop.managers.leases, resource.id
    held = await loop.managers.leases.ask(loop.owner, an_ask(resource.id))
    assert held.lease is not None
    return loop, resource, held.lease


async def parked_in_line(loop: Loop) -> tuple[UUID, Step, Park]:
    """A session whose tool asked for the held resource and whose turn then
    ended: it parks in line, with the call's request and the park."""
    session_id = await loop.start("reserver")
    await loop.say(session_id, "Take the resource.")
    loop.anthropic.add(reply(call("reserve")))
    loop.anthropic.add(reply(said("I am in line for it, and I wait.")))
    run = await loop.loops.run(loop.owner, session_id)
    assert run.end is RunEnd.PARKED and run.park is not None, run
    (request,) = [s for s in await loop.history(session_id) if s.type is StepType.TOOL_REQUEST]
    return session_id, request, run.park


async def notices(loop: Loop) -> list[tuple[UUID, UUID]]:
    """Every queued `LEASE_NOTICE` item, claimed from the storage: its
    session and its request."""
    work = loop.storage.get_work_storage()
    found: list[tuple[UUID, UUID]] = []
    while True:
        claimed = await work.claim_next(
            "default", [WorkKind.LEASE_NOTICE], "test", timedelta(seconds=30)
        )
        if claimed is None:
            return found
        _, item = claimed
        payload = LeaseNoticePayload.model_validate(dict(item.payload))
        found.append((item.target_id, payload.request_id))


async def unlocked(loop: Loop, ctx: TenantContext, session_id: UUID) -> AgentSession:
    """What the `LEASE_NOTICE` handler does: a session parked in line is
    unlocked."""
    session = await loop.managers.agent_sessions.get_session(ctx, session_id)
    park = session.park
    if session.status is not SessionStatus.PARKED or park is None or park.line is None:
        return session
    return await loop.managers.agent_sessions.wake_session(ctx, session_id, park)


async def test_a_session_parks_in_line_and_wakes_with_its_lease_with_no_model_call_between(
    tmp_path: Path,
) -> None:
    loop, resource, held = await held_resource(tmp_path)

    session_id, request, park = await parked_in_line(loop)

    # The stop reason names the request, its place, and its estimate, and
    # no time: only the grant, or the request's end, clears it.
    assert (park.reason, park.unlock, park.retry_at) == (ParkReason.RESOURCE, "grant", None)
    assert park.line is not None
    assert (park.line.request_id, park.line.resource_id, park.line.place) == (
        request.id,
        resource.id,
        1,
    )
    assert park.line.estimate_seconds is not None
    session = await loop.managers.agent_sessions.get_session(loop.owner, session_id)
    assert (session.status, session.park) == (SessionStatus.PARKED, park)
    assert len(loop.anthropic.calls) == 2

    # Woken while the request still waits, the loop parks in line again,
    # and calls no model.
    await loop.managers.agent_sessions.wake_session(loop.owner, session_id, park)
    again = await loop.loops.run(loop.owner, session_id)
    assert again.end is RunEnd.PARKED and again.park is not None
    assert again.park.line is not None and again.park.line.request_id == request.id
    assert len(loop.anthropic.calls) == 2, "no model call while the ask waits"

    # The resource frees: the grant asks for the session's notice in its own
    # commit, and the handler unlocks the loop parked in line.
    await loop.managers.leases.release(loop.owner, held.id)
    assert await notices(loop) == [(session_id, request.id)]
    assert (await unlocked(loop, loop.owner, session_id)).status is SessionStatus.PENDING
    loop.anthropic.add(reply(said("I hold it now.")))

    run = await loop.loops.run(loop.owner, session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED
    assert len(loop.anthropic.calls) == 3, "one model call, after the grant"
    lease = (await loop.managers.leases.get_request(loop.owner, request.id)).lease
    assert lease is not None and lease.status is LeaseStatus.ACTIVE
    sent = str(loop.anthropic.calls[2].model_dump())
    assert f"lease {lease.id}, token {lease.token}" in sent, "the lease is in its notice"


async def test_a_grant_between_the_read_and_the_park_wakes_the_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop, _, held = await held_resource(tmp_path)
    sessions = loop.managers.agent_sessions
    park_as_written = sessions.park

    async def freed_first(
        ctx: TenantContext, session_id: UUID, epoch: int, loop_id: UUID, park: Park
    ) -> object:
        # The resource frees after the run read its ask waiting, and before
        # its park lands: the grant finds no park to clear.
        if park.line is not None:
            await loop.managers.leases.release(loop.owner, held.id)
        return await park_as_written(ctx, session_id, epoch, loop_id, park)

    monkeypatch.setattr(sessions, "park", freed_first)

    session_id, request, _ = await parked_in_line(loop)

    # The run read its ask again once parked, and cleared the park itself.
    session = await sessions.get_session(loop.owner, session_id)
    assert session.status is SessionStatus.PENDING
    loop.anthropic.add(reply(said("I hold it now.")))
    run = await loop.loops.run(loop.owner, session_id)
    assert run.outcome is LoopOutcome.SUCCEEDED
    lease = (await loop.managers.leases.get_request(loop.owner, request.id)).lease
    assert lease is not None
    assert f"lease {lease.id}" in str(loop.anthropic.calls[2].model_dump())


async def test_a_session_cancelled_in_line_leaves_it_and_the_next_request_is_granted(
    tmp_path: Path,
) -> None:
    loop, resource, held = await held_resource(tmp_path)
    session_id, request, _ = await parked_in_line(loop)
    behind = await loop.managers.leases.ask(loop.owner, an_ask(resource.id))
    assert behind.place == 2

    await loop.managers.steps.append_inputs(
        loop.owner,
        session_id,
        [
            Step(
                id=new_id(),
                created_at=loop.clock(),
                session_id=session_id,
                loop_id=new_id(),
                type=StepType.CONTROL,
                actor=Actor.PERSON,
                origin=Origin.PORTAL,
                header=ControlHeader(command=ControlCommand.CANCEL),
            )
        ],
    )
    run = await loop.loops.run(loop.owner, session_id)

    assert run.outcome is LoopOutcome.CANCELLED
    left = (await loop.managers.leases.get_request(loop.owner, request.id)).request
    assert (left.status, left.end_reason) == (RequestStatus.CANCELLED, EndReason.WAITER_GONE)
    line = await loop.managers.leases.line(loop.owner, resource.id)
    assert [r.id for r in line.requests] == [behind.request.id]
    await loop.managers.leases.release(loop.owner, held.id)
    granted = await loop.managers.leases.get_request(loop.owner, behind.request.id)
    assert granted.lease is not None, "the next request is granted"
    assert await notices(loop) == [], "a session that left is told nothing"
    assert len(loop.anthropic.calls) == 2


async def test_a_lease_revoked_after_its_loop_ended_is_told_in_the_next_loops_first_call(
    tmp_path: Path,
) -> None:
    loop, _, held = await held_resource(tmp_path)
    session_id, request, _ = await parked_in_line(loop)
    await loop.managers.leases.release(loop.owner, held.id)
    await unlocked(loop, loop.owner, session_id)
    loop.anthropic.add(reply(said("I hold it now.")))
    first = await loop.loops.run(loop.owner, session_id)
    assert first.outcome is LoopOutcome.SUCCEEDED
    lease = (await loop.managers.leases.get_request(loop.owner, request.id)).lease
    assert lease is not None

    # The loop that took the lease has ended; a manager takes it back.
    await loop.managers.leases.revoke(loop.owner, lease.id)
    await loop.say(session_id, "Go on with the work.")
    loop.anthropic.add(reply(said("I no longer hold it.")), reply(said("Still not mine.")))
    second = await loop.loops.run(loop.owner, session_id)

    assert second.outcome is LoopOutcome.SUCCEEDED
    told = f"The lease {lease.id} granted to your request {request.id} was revoked"
    assert told in str(loop.anthropic.calls[3].model_dump()), "the next loop's first call"
    await loop.say(session_id, "And now?")
    await loop.loops.run(loop.owner, session_id)
    history = await loop.history(session_id)
    notices_told = [s for s in history if s.actor is Actor.ENGINE and told in str(s.content)]
    assert len(notices_told) == 1, "told once"


async def test_a_lease_that_ended_before_its_session_resumed_is_told_ended_never_granted(
    tmp_path: Path,
) -> None:
    loop, _, held = await held_resource(tmp_path)
    session_id, request, _ = await parked_in_line(loop)
    await loop.managers.leases.release(loop.owner, held.id)
    lease = (await loop.managers.leases.get_request(loop.owner, request.id)).lease
    assert lease is not None

    # Before the session resumes, the lease runs past its expiry and the
    # skew margin, and the sweep's write ends it.
    later = lease.expires_at + timedelta(minutes=5)
    expired = await loop.storage.get_lease_storage().end_lease(
        loop.owner.org_id, lease.id, LeaseStatus.EXPIRED, later, loop.owner.user_id, 60.0, (), later
    )
    assert expired is not None and expired.status is LeaseStatus.EXPIRED
    await unlocked(loop, loop.owner, session_id)
    loop.anthropic.add(reply(said("It ran out before I used it.")))
    run = await loop.loops.run(loop.owner, session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED
    sent = str(loop.anthropic.calls[2].model_dump())
    assert f"The lease {lease.id} granted to your request {request.id} ran past its expiry" in sent
    assert "Go on with the work" not in sent, "never told to go on under a dead token"


def a_standing(status: RequestStatus, lease: LeaseStatus | None = None) -> Standing:
    request = lines.in_line(an_ask(new_id()), new_id(), new_id())
    granted = None
    if lease is not None:
        now = utcnow()
        granted = Lease(
            id=new_id(),
            created_at=now,
            updated_at=now,
            created_by=new_id(),
            updated_by=new_id(),
            resource_id=new_id(),
            request_id=request.id,
            holder_id=new_id(),
            token=7,
            term_seconds=60,
            expires_at=now + timedelta(seconds=60),
            status=lease,
        )
    reason = EndReason.RETIRED if status is RequestStatus.CANCELLED else None
    return Standing(
        request=request.model_copy(update={"status": status, "end_reason": reason}),
        lease=granted,
        place=1 if status is RequestStatus.WAITING else None,
    )


def an_ask_of(request_id: UUID, granted: bool) -> lines.Ask:
    return lines.Ask(request_id, granted, new_id(), utcnow())


def test_an_ask_is_the_calls_and_its_session_waits_on_it() -> None:
    session_id, key = new_id(), new_id()
    ask = lines.in_line(an_ask(new_id()), session_id, key)
    assert (ask.id, ask.idempotency_key) == (key, key)
    assert (ask.waiter_kind, ask.waiter_id) == (WaiterKind.SESSION, session_id)


def test_each_answer_is_told_once_and_a_grant_the_call_answered_with_never() -> None:
    waiting = a_standing(RequestStatus.WAITING)
    assert lines.untold(waiting, an_ask_of(waiting.request.id, False), ()) == []

    granted = a_standing(RequestStatus.GRANTED, LeaseStatus.ACTIVE)
    ask = an_ask_of(granted.request.id, False)
    ((step_id, text),) = lines.untold(granted, ask, ())
    assert step_id == lines.notice_id(ask, lines.Answer.GRANTED)
    assert "token 7" in text
    assert lines.untold(granted, ask, {step_id}) == []
    assert lines.untold(granted, an_ask_of(granted.request.id, True), ()) == []

    revoked = a_standing(RequestStatus.GRANTED, LeaseStatus.REVOKED)
    ask = an_ask_of(revoked.request.id, True)
    told = [i for i, _ in lines.untold(revoked, ask, ())]
    assert told == [lines.notice_id(ask, lines.Answer.REVOKED)]
    assert lines.done(ask, told) and not lines.done(ask, ())

    # A lease that ended before its grant was told is told as ended alone.
    for status, answer in (
        (LeaseStatus.EXPIRED, lines.Answer.EXPIRED),
        (LeaseStatus.RELEASED, lines.Answer.RELEASED),
    ):
        ended = a_standing(RequestStatus.GRANTED, status)
        ask = an_ask_of(ended.request.id, False)
        ((step_id, text),) = lines.untold(ended, ask, ())
        assert step_id == lines.notice_id(ask, answer)
        assert "token 7 is refused" in text and "Go on" not in text

    ended = a_standing(RequestStatus.CANCELLED)
    ((_, why),) = lines.untold(ended, an_ask_of(ended.request.id, False), ())
    assert "the resource it named was retired" in why
    ((_, why),) = lines.untold(a_standing(RequestStatus.EXPIRED), an_ask_of(new_id(), False), ())
    assert "it waited past its wait" in why
