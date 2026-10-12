"""Agent session routes: start a session on a kind, read it, send it a
message or a control, decide a tool call it waits on, clear the park a
person clears, and read its history a page at a time. Each function is
one call into the agent sessions service; every create runs under the
idempotency record, so a retried send is one step. The loop runs in the
session runner: a route writes what a person said and answers once it is
durable.

Each park a person clears has its route, held to the permission its
manager checks, and clearing it wakes the session: a message answers a
question, a decision an approval, a moved deadline the deadline, an
assignment a principal who no longer holds, a giving back the hand-over
its holder holds, a job's completion its job, and an unlock the step
guard, a spender, a lost workspace, and a key or a price fixed outside."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from acme.services.api.gateway.auth import Ctx
from acme.services.api.gateway.idempotency import Idem
from acme.services.api.gateway.resolve import AgentSessionsService
from acme.services.api.types.agent_sessions import (
    AgentSessionView,
    ControlRequest,
    DeadlineRequest,
    DecisionRequest,
    GiveBackRequest,
    JobCompletionRequest,
    MessageRequest,
    StartSessionRequest,
    StepPageView,
    StepView,
)
from acme.services.api.types.common import LIMIT_DEFAULT

router = APIRouter(prefix="/agent-sessions", tags=["agent_sessions"])


@router.post("", response_model=AgentSessionView, status_code=201)
async def start_session(
    ctx: Ctx, sessions: AgentSessionsService, body: StartSessionRequest, idem: Idem
) -> Response:
    """A session on the kind, idle until a message wakes it."""
    return await idem.run(201, lambda attempt: sessions.start_session(ctx, body, attempt.target_id))


@router.get("/{session_id}", response_model=AgentSessionView)
async def get_session(
    ctx: Ctx, sessions: AgentSessionsService, session_id: UUID
) -> AgentSessionView:
    return await sessions.get_session(ctx, session_id)


@router.post("/{session_id}/messages", response_model=StepView, status_code=201)
async def send_message(
    ctx: Ctx,
    sessions: AgentSessionsService,
    session_id: UUID,
    body: MessageRequest,
    idem: Idem,
) -> Response:
    """The message as stored, durable when this answers."""
    return await idem.run(
        201, lambda attempt: sessions.send_message(ctx, session_id, body, attempt.target_id)
    )


@router.post("/{session_id}/controls", response_model=StepView, status_code=201)
async def send_control(
    ctx: Ctx,
    sessions: AgentSessionsService,
    session_id: UUID,
    body: ControlRequest,
    idem: Idem,
) -> Response:
    """The control as stored, out of band: it never waits behind a message."""
    return await idem.run(
        201, lambda attempt: sessions.send_control(ctx, session_id, body, attempt.target_id)
    )


@router.post("/{session_id}/calls/{request_seq}/decision", response_model=StepView, status_code=201)
async def decide_call(
    ctx: Ctx,
    sessions: AgentSessionsService,
    session_id: UUID,
    request_seq: int,
    body: DecisionRequest,
    idem: Idem,
) -> Response:
    """An approval or a denial of the tool call at `request_seq`, as stored."""
    return await idem.run(
        201, lambda attempt: sessions.decide_call(ctx, session_id, request_seq, body)
    )


# A deadline is set whole by a PUT, so a retry after a lost answer sets the
# same deadline again and the route takes no Idempotency-Key.
@router.put("/{session_id}/deadline", response_model=AgentSessionView)
async def set_deadline(
    ctx: Ctx, sessions: AgentSessionsService, session_id: UUID, body: DeadlineRequest
) -> AgentSessionView:
    """The deadline of the session's tree, moved for every session of it.
    One past now, or null, unlocks each session parked on the deadline."""
    return await sessions.set_deadline(ctx, session_id, body)


# The caller becomes the principal, so a retry makes the same assignment.
@router.put("/{session_id}/principal", response_model=AgentSessionView)
async def assign_principal(
    ctx: Ctx, sessions: AgentSessionsService, session_id: UUID
) -> AgentSessionView:
    """The caller becomes the principal the session's tool calls run under,
    and a loop parked on a principal who no longer holds goes on."""
    return await sessions.assign_principal(ctx, session_id)


@router.post("/{session_id}/take-over", response_model=AgentSessionView)
async def take_over(ctx: Ctx, sessions: AgentSessionsService, session_id: UUID) -> AgentSessionView:
    """The caller, in person, takes the environment to work by hand: the
    loop parks on a hand-over they hold. 409 when another person holds it."""
    return await sessions.take_over(ctx, session_id)


@router.post("/{session_id}/give-back", response_model=AgentSessionView)
async def give_back(
    ctx: Ctx,
    sessions: AgentSessionsService,
    session_id: UUID,
    body: GiveBackRequest,
    idem: Idem,
) -> Response:
    """The holder, in person, gives the environment back with what they
    did there, and the loop goes on: 403 for anyone else."""
    return await idem.run(200, lambda attempt: sessions.give_back(ctx, session_id, body))


@router.post("/{session_id}/jobs/{key}/completion", response_model=AgentSessionView)
async def complete_job(
    ctx: Ctx,
    sessions: AgentSessionsService,
    session_id: UUID,
    key: UUID,
    body: JobCompletionRequest,
    idem: Idem,
) -> Response:
    """How the job of `key` ended, from the system it ran on: the loop that
    waits on it goes on. 404 for a job the session does not wait on."""
    return await idem.run(200, lambda attempt: sessions.complete_job(ctx, session_id, key, body))


@router.get("/{session_id}/steps", response_model=StepPageView)
async def get_steps(
    ctx: Ctx,
    sessions: AgentSessionsService,
    session_id: UUID,
    after_seq: Annotated[int, Query(ge=0)] = 0,
    limit: int = LIMIT_DEFAULT,
) -> StepPageView:
    """The history in order, strictly after `after_seq`."""
    return await sessions.get_steps(ctx, session_id, after_seq, limit)
