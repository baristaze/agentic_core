"""Agent session routes: start a session on a kind, read it, send it a
message or a control, decide a tool call it waits on, and read its
history a page at a time. Each function is one call into the agent
sessions service; every create runs under the idempotency record, so a
retried send is one step. The loop runs in the session runner: a route
writes what a person said and answers once it is durable."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from acme.services.api.gateway.auth import Ctx
from acme.services.api.gateway.idempotency import Idem
from acme.services.api.gateway.resolve import AgentSessionsService
from acme.services.api.types.agent_sessions import (
    AgentSessionView,
    ControlRequest,
    DecisionRequest,
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
