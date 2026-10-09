"""A job's call in line for a leased resource, over the memory storage and
the scripted provider: a job tool whose work runs on a held resource asks
in line, and its loop parks on `resource` at once, naming its place and
the job; the grant starts the job in its own commit, the park moves to
the job's, and the job's result answers the call, with no model call and
no notice between the ask and the answer; a free resource's grant starts
the job at once; a grant between the read and the park still reaches the
session; and a session cancelled while its job waits in line leaves the
line, so no job starts."""

from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from contracts.loops import ALLOWED, Loop, call, loop_over, reply, said
from unit.test_loop_lines import an_ask, notices, unlocked

from acme.om.agent_sessions.types.agent_session import SessionStatus
from acme.om.agents import line_rules as lines
from acme.om.agents.types.kind import AgentKind, DoneRule, TreeLimits
from acme.om.agents.types.run import RunEnd
from acme.om.attribution.types.authority import AuthorityMode
from acme.om.base import Platform, new_id, utcnow
from acme.om.context import TenantContext
from acme.om.leases import LeasesManagerInterface
from acme.om.leases.impl.kinds import NoopResourceKindImpl
from acme.om.leases.types.lease import JobClaim, Lease
from acme.om.leases.types.request import EndReason, LeaseRequest, RequestStatus
from acme.om.leases.types.resource import Resource, ResourceKind
from acme.om.outbox.types.row import OutboxRow, outbox_row
from acme.om.steps.types.content import TextBlock
from acme.om.steps.types.header import (
    ControlCommand,
    ControlHeader,
    LoopOutcome,
    Park,
    ParkReason,
    ToolFailure,
    ToolResponseHeader,
)
from acme.om.steps.types.step import Actor, Origin, Step, StepType
from acme.om.tools.tool import JobToolInterface, ToolRuntime
from acme.om.tools.types.call import JobCompletion, JobHandle, JobStarted
from acme.om.tools.types.policy import Target
from acme.om.tools.types.tool import Effect, ToolClass, ToolInput, ToolMode, ToolSpec
from acme.om.work.types.work_item import WorkItem, WorkKind, work_row_kind


class Docking(ToolInput):
    """No input: the job runs on the one resource it was given."""


class Dock(JobToolInterface):
    """A product's job tool whose work runs on a leased resource: its run
    asks in line, as its session's waiter, and names the request, whose
    grant starts the work. It keeps each cancel."""

    def __init__(self) -> None:
        self._spec = ToolSpec(
            name="dock",
            description="Runs the job on the resource.",
            input_model=Docking,
            output_model=JobStarted,
            timeout=timedelta(hours=2),
            authorization_class=ToolClass.WRITE,
            effect=Effect.IDEMPOTENT,
            interruptible=True,
            mode=ToolMode.JOB,
        )
        self.leases: LeasesManagerInterface | None = None
        self.resource_id: UUID | None = None
        self.cancelled: list[JobHandle] = []

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
        standing = await self.leases.ask(ctx, ask)
        return JobStarted(handle="dock-1", request_id=standing.request.id)

    async def cancel(self, ctx: TenantContext, job: JobHandle) -> None:
        self.cancelled.append(job)


DOCKER = AgentKind(
    name="docker",
    version=1,
    tools=("lookup", "dock"),
    done_rule=DoneRule.ANSWER,
    authority=AuthorityMode.DELEGATED,
    tree=TreeLimits(height=2, count=4),
    prompts=("You run the job on the resource, and say what it found.",),
    policy=ALLOWED,
)


def starts_the_job(
    self: NoopResourceKindImpl,
    ctx: TenantContext,
    resource: Resource,
    request: LeaseRequest,
    lease: Lease,
) -> tuple[OutboxRow, ...]:
    """A product's kind whose grant starts a job: one work item, keyed by
    the lease it runs under."""
    return (outbox_row(ctx, work_row_kind(WorkKind.NOOP), lease.id, {}),)


async def a_resource(loop: Loop, dock: Dock, *, held: bool) -> tuple[Resource, Lease | None]:
    """The resource the dock's job runs on, held by its owner when `held`."""
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
    dock.leases, dock.resource_id = loop.managers.leases, resource.id
    if not held:
        return resource, None
    holding = await loop.managers.leases.ask(loop.owner, an_ask(resource.id))
    assert holding.lease is not None
    return resource, holding.lease


async def docking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, held: bool = True
) -> tuple[Loop, Dock, Resource, Lease | None]:
    """A loop whose `docker` kind runs the dock's job on a resource, whose
    grant from now on starts that job."""
    dock = Dock()
    loop = loop_over(tmp_path, kinds=(DOCKER,), extra=(dock,))
    resource, holding = await a_resource(loop, dock, held=held)
    monkeypatch.setattr(NoopResourceKindImpl, "grant_rows", starts_the_job)
    return loop, dock, resource, holding


async def asked(loop: Loop) -> tuple[UUID, Step, Park]:
    """A session whose model called the dock: its run parks, with the call's
    request and the park."""
    session_id = await loop.start("docker")
    await loop.say(session_id, "Run the job.")
    loop.anthropic.add(reply(call("dock")))
    run = await loop.loops.run(loop.owner, session_id)
    assert run.end is RunEnd.PARKED and run.park is not None, run
    (request,) = [s for s in await loop.history(session_id) if s.type is StepType.TOOL_REQUEST]
    return session_id, request, run.park


async def the_job(loop: Loop, lease: Lease) -> WorkItem | None:
    """The job the lease's grant queued, claimed as its worker claims it,
    or None when no grant queued one."""
    work = loop.storage.get_work_storage()
    claimed = await work.claim_next("default", [WorkKind.NOOP], "dock", timedelta(minutes=5))
    if claimed is None:
        return None
    _, item = claimed
    assert item.idempotency_key == lease.job_key
    return item


def said_in(answer: Step) -> str:
    parts = answer.as_tool_response().parts
    return "".join(part.text for part in parts if isinstance(part, TextBlock))


async def test_a_job_tool_waits_in_line_and_its_grant_starts_the_job_with_no_model_call_between(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop, _, resource, holding = await docking(tmp_path, monkeypatch)
    assert holding is not None

    session_id, request, park = await asked(loop)

    # The call parks in line at once: the stop reason names the request, its
    # place, and the job it waits to start, and tries again at the job's
    # deadline.
    assert (park.reason, park.unlock) == (ParkReason.RESOURCE, "grant")
    assert park.line is not None and park.job is not None and park.retry_at is not None
    assert (park.line.request_id, park.line.resource_id, park.line.place) == (
        request.id,
        resource.id,
        1,
    )
    assert (park.job.key, park.job.handle) == (request.id, "dock-1")
    assert len(loop.anthropic.calls) == 1

    # Woken while the request still waits, the loop parks in line again,
    # and calls no model.
    await loop.managers.agent_sessions.wake_session(loop.owner, session_id, park)
    again = await loop.loops.run(loop.owner, session_id)
    assert again.end is RunEnd.PARKED and again.park is not None
    assert again.park.line is not None and again.park.job is not None
    assert len(loop.anthropic.calls) == 1, "no model call while the job waits in line"

    # The resource frees: the grant queues the job and the session's notice
    # in its own commit, and the handler unlocks the loop parked in line.
    await loop.managers.leases.release(loop.owner, holding.id)
    lease = (await loop.managers.leases.get_request(loop.owner, request.id)).lease
    assert lease is not None and lease.job_key is not None
    assert await notices(loop) == [(session_id, request.id)]
    assert (await unlocked(loop, loop.owner, session_id)).status is SessionStatus.PENDING

    moved = await loop.loops.run(loop.owner, session_id)

    # The park moves to the job's, with no model call.
    assert moved.end is RunEnd.PARKED and moved.park is not None
    assert (moved.park.reason, moved.park.unlock, moved.park.line) == (
        ParkReason.JOB,
        str(request.id),
        None,
    )
    assert moved.park.job == park.job and moved.park.retry_at == park.retry_at
    assert len(loop.anthropic.calls) == 1, "no model call when the grant starts the job"

    # The job's worker starts the lease, and its result answers the call.
    job = await the_job(loop, lease)
    assert job is not None and job.claim_token is not None
    started = await loop.managers.leases.start(
        loop.owner, lease.id, JobClaim(token=lease.token, claim_token=job.claim_token)
    )
    assert started.started_at is not None
    woken = await loop.loops.complete_job(
        loop.owner,
        session_id,
        JobCompletion(key=request.id, handle="dock-1", text="the job found 3 faults"),
    )
    assert woken.status is SessionStatus.PENDING
    loop.anthropic.add(reply(said("It found 3 faults.")))

    run = await loop.loops.run(loop.owner, session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED
    assert len(loop.anthropic.calls) == 2, "one model call, after the job's result"
    steps = await loop.history(session_id)
    (answer,) = [s for s in steps if s.responds_to == request.id]
    assert said_in(answer) == "the job found 3 faults"
    assert not [s for s in steps if s.actor is Actor.ENGINE and s.type.is_input()], "no notice"
    assert "the job found 3 faults" in str(loop.anthropic.calls[1].model_dump())


async def test_a_free_resources_grant_starts_the_job_and_the_loop_parks_on_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop, _, _, _ = await docking(tmp_path, monkeypatch, held=False)

    _, request, park = await asked(loop)

    assert (park.reason, park.unlock, park.line) == (ParkReason.JOB, str(request.id), None)
    lease = (await loop.managers.leases.get_request(loop.owner, request.id)).lease
    assert lease is not None
    assert await the_job(loop, lease) is not None, "the grant queued the job"


async def test_a_grant_between_the_read_and_the_park_still_reaches_the_jobs_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop, _, _, holding = await docking(tmp_path, monkeypatch)
    assert holding is not None
    sessions = loop.managers.agent_sessions
    park_as_written = sessions.park

    async def freed_first(
        ctx: TenantContext, session_id: UUID, epoch: int, loop_id: UUID, park: Park
    ) -> object:
        # The resource frees after the run read the request waiting, and
        # before its park lands: the grant finds no park to clear.
        if park.line is not None:
            await loop.managers.leases.release(loop.owner, holding.id)
        return await park_as_written(ctx, session_id, epoch, loop_id, park)

    monkeypatch.setattr(sessions, "park", freed_first)

    session_id, _, _ = await asked(loop)

    # The run read the request again once parked, and cleared the park.
    assert (await sessions.get_session(loop.owner, session_id)).status is SessionStatus.PENDING
    run = await loop.loops.run(loop.owner, session_id)
    assert run.park is not None and run.park.reason is ParkReason.JOB
    assert len(loop.anthropic.calls) == 1


async def test_a_session_cancelled_while_its_job_waits_in_line_leaves_it_and_no_job_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop, dock, resource, holding = await docking(tmp_path, monkeypatch)
    assert holding is not None
    session_id, request, _ = await asked(loop)

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
    assert (await loop.managers.leases.line(loop.owner, resource.id)).requests == ()
    assert [job.key for job in dock.cancelled] == [request.id], "the job is cancelled"
    (answer,) = [s for s in await loop.history(session_id) if s.responds_to == request.id]
    assert isinstance(answer.header, ToolResponseHeader)
    assert answer.header.failure is ToolFailure.INTERRUPTED

    # The resource frees, and no grant starts a job for the session gone.
    await loop.managers.leases.release(loop.owner, holding.id)
    standing = await loop.managers.leases.get_request(loop.owner, request.id)
    assert standing.lease is None
    work = loop.storage.get_work_storage()
    queued = await work.claim_next("default", [WorkKind.NOOP], "dock", timedelta(minutes=5))
    assert queued is None, "no job starts"
    assert await notices(loop) == []
    assert len(loop.anthropic.calls) == 1
