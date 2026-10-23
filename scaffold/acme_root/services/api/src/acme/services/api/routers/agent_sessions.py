"""Agent session routes: start a session on a kind, list the ones a
person takes part in or sees, join one or leave it, switch who sees it,
hand its steer on, send it a message or a control, decide a tool call it
waits on, clear the park a person clears, and read its history and its
tool_outputs a page at a time. Each function is one call into the agent
sessions service; every create runs under the idempotency record, so a
retried send is one step. The loop runs in the session runner: a route
writes what a person said and answers once it is durable. A session the
caller does not see answers 404, as one that never existed.

Each park a person clears has its route, held to the permission its
manager checks, and clearing it wakes the session: a message answers a
question, a decision an approval, a moved deadline the deadline, an
assignment a principal who no longer holds, a giving back the hand-over
its holder holds, a job's completion its job, and an unlock the step
guard, a spender, a lost workspace, and a key or a price fixed outside.

On the operator plane (`routers/tenancy.py`), an operator token that may
read reads what one session's model calls used and cost."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Query, Response

from acme.om.agent_sessions.types.agent_session import AgentSessionStatus, Among
from acme.services.api.gateway.admin import OperatorCtx
from acme.services.api.gateway.admission import HOLDER_CALL
from acme.services.api.gateway.auth import Ctx
from acme.services.api.gateway.idempotency import Idem
from acme.services.api.gateway.resolve import AgentSessionsService
from acme.services.api.types.agent_sessions import (
    AgentSessionPageView,
    AgentSessionUsageView,
    AgentSessionView,
    AgentStepPageView,
    AgentStepView,
    ControlRequest,
    DeadlineRequest,
    DecisionRequest,
    GiveBackRequest,
    JobCompletionRequest,
    MessageRequest,
    PrincipalRequest,
    StartAgentSessionRequest,
    ToolOutputPageView,
    VisibilityRequest,
)
from acme.services.api.types.common import LIMIT_DEFAULT

router = APIRouter(prefix="/agent-sessions", tags=["agent_sessions"])
operator = APIRouter(prefix="/admin", tags=["agent_sessions"])


@router.post("", response_model=AgentSessionView, status_code=201)
async def start_agent_session(
    ctx: Ctx, sessions: AgentSessionsService, body: StartAgentSessionRequest, idem: Idem
) -> Response:
    """A session on the kind, idle until a message wakes it."""
    return await idem.run(
        201, lambda attempt: sessions.start_agent_session(ctx, body, attempt.target_id)
    )


@router.get("", response_model=AgentSessionPageView)
async def list_agent_sessions(
    ctx: Ctx,
    sessions: AgentSessionsService,
    among: Among = Among.VISIBLE,
    status: AgentSessionStatus | None = None,
    after: UUID | None = None,
    limit: int = LIMIT_DEFAULT,
) -> AgentSessionPageView:
    """The org's root sessions by id, after `after`: the ones the caller
    made or takes part in (`mine`), or every one they see (`visible`)."""
    return await sessions.list_agent_sessions(ctx, among, status, after, limit)


@router.get("/{agent_session_id}", response_model=AgentSessionView)
async def get_agent_session(
    ctx: Ctx, sessions: AgentSessionsService, agent_session_id: UUID
) -> AgentSessionView:
    return await sessions.get_agent_session(ctx, agent_session_id)


@router.post("/{agent_session_id}/messages", response_model=AgentStepView, status_code=201)
async def send_message(
    ctx: Ctx,
    sessions: AgentSessionsService,
    agent_session_id: UUID,
    body: MessageRequest,
    idem: Idem,
) -> Response:
    """The message as stored, durable when this answers."""
    return await idem.run(
        201, lambda attempt: sessions.send_message(ctx, agent_session_id, body, attempt.target_id)
    )


@router.post("/{agent_session_id}/controls", response_model=AgentStepView, status_code=201)
async def send_control(
    ctx: Ctx,
    sessions: AgentSessionsService,
    agent_session_id: UUID,
    body: ControlRequest,
    idem: Idem,
) -> Response:
    """The control as stored, out of band: it never waits behind a message."""
    return await idem.run(
        201, lambda attempt: sessions.send_control(ctx, agent_session_id, body, attempt.target_id)
    )


@router.post(
    "/{agent_session_id}/calls/{request_seq}/decision",
    response_model=AgentStepView,
    status_code=201,
)
async def decide_call(
    ctx: Ctx,
    sessions: AgentSessionsService,
    agent_session_id: UUID,
    request_seq: int,
    body: DecisionRequest,
    idem: Idem,
) -> Response:
    """An approval or a denial of the tool call at `request_seq`, as stored."""
    return await idem.run(
        201, lambda attempt: sessions.decide_call(ctx, agent_session_id, request_seq, body)
    )


# A deadline is set whole by a PUT, so a retry after a lost answer sets the
# same deadline again and the route takes no Idempotency-Key.
@router.put("/{agent_session_id}/deadline", response_model=AgentSessionView)
async def set_deadline(
    ctx: Ctx, sessions: AgentSessionsService, agent_session_id: UUID, body: DeadlineRequest
) -> AgentSessionView:
    """The deadline of the session's tree, moved for every session of it.
    One past now, or null, unlocks each session parked on the deadline."""
    return await sessions.set_deadline(ctx, agent_session_id, body)


# Who takes part, who sees it, and who steers it are each set whole by a
# PUT or a DELETE, so a retry after a lost answer sets the same again and
# the routes take no Idempotency-Key.
@router.put("/{agent_session_id}/participants/{user_id}", response_model=AgentSessionView)
async def add_participant(
    ctx: Ctx, sessions: AgentSessionsService, agent_session_id: UUID, user_id: UUID
) -> AgentSessionView:
    """`user_id` takes part in the session: any member joins an org-wide
    one, and its creator adds anyone. A private one is joined only by whom
    its creator adds and by its steerer: 403 for any other self-join,
    `manage_agent_sessions` included, and for anyone else's add."""
    return await sessions.add_participant(ctx, agent_session_id, user_id)


@router.delete("/{agent_session_id}/participants/{user_id}", response_model=AgentSessionView)
async def remove_participant(
    ctx: Ctx, sessions: AgentSessionsService, agent_session_id: UUID, user_id: UUID
) -> AgentSessionView:
    """`user_id` takes no part in the session: its creator removes anyone,
    and a participant leaves on their own. 403 for anyone else, 409 for
    its steerer until the steer is handed on."""
    return await sessions.remove_participant(ctx, agent_session_id, user_id)


@router.put("/{agent_session_id}/visibility", response_model=AgentSessionView)
async def switch_visibility(
    ctx: Ctx, sessions: AgentSessionsService, agent_session_id: UUID, body: VisibilityRequest
) -> AgentSessionView:
    """Its creator switches who sees the session, recorded as a step: 403
    for anyone else."""
    return await sessions.switch_visibility(ctx, agent_session_id, body)


@router.put("/{agent_session_id}/principal", response_model=AgentSessionView)
async def assign_principal(
    ctx: Ctx,
    sessions: AgentSessionsService,
    agent_session_id: UUID,
    body: Annotated[PrincipalRequest | None, Body()] = None,
) -> AgentSessionView:
    """The steer moves, recorded as a step: its steerer hands it to the
    participant the body names, or with no body the caller takes it, for a
    steerer who lapsed with `manage_agent_sessions`. Its tool calls run under the
    steerer, and a loop parked on a principal who no longer holds goes on.
    403 for anyone else, 422 for one who takes no part in it."""
    return await sessions.assign_principal(ctx, agent_session_id, body)


@router.post("/{agent_session_id}/take-over", response_model=AgentSessionView)
async def take_over(
    ctx: Ctx, sessions: AgentSessionsService, agent_session_id: UUID
) -> AgentSessionView:
    """The caller, in person, takes the environment to work by hand: the
    loop parks on a hand-over they hold. 409 when another person holds it."""
    return await sessions.take_over(ctx, agent_session_id)


@router.post("/{agent_session_id}/give-back", response_model=AgentSessionView)
async def give_back(
    ctx: Ctx,
    sessions: AgentSessionsService,
    agent_session_id: UUID,
    body: GiveBackRequest,
    idem: Idem,
) -> Response:
    """The holder, in person, gives the environment back with what they
    did there, and the loop goes on: 403 for anyone else."""
    return await idem.run(200, lambda attempt: sessions.give_back(ctx, agent_session_id, body))


@router.post(
    "/{agent_session_id}/jobs/{key}/completion",
    response_model=AgentSessionView,
    dependencies=[HOLDER_CALL],
)
async def complete_job(
    ctx: Ctx,
    sessions: AgentSessionsService,
    agent_session_id: UUID,
    key: UUID,
    body: JobCompletionRequest,
    idem: Idem,
) -> Response:
    """How the job of `key` ended, from the system it ran on: the loop that
    waits on it goes on. 404 for a job the session does not wait on. It is a
    holder's call: the loop closes the job as timed out at its deadline, so a
    completion refused until then loses the job's work."""
    return await idem.run(
        200, lambda attempt: sessions.complete_job(ctx, agent_session_id, key, body)
    )


@router.get("/{agent_session_id}/steps", response_model=AgentStepPageView)
async def get_agent_steps(
    ctx: Ctx,
    sessions: AgentSessionsService,
    agent_session_id: UUID,
    after_seq: Annotated[int, Query(ge=0)] = 0,
    limit: int = LIMIT_DEFAULT,
) -> AgentStepPageView:
    """The history in order, strictly after `after_seq`."""
    return await sessions.get_agent_steps(ctx, agent_session_id, after_seq, limit)


@router.get("/{agent_session_id}/tool-outputs/{tool_output_id}", response_model=ToolOutputPageView)
async def get_tool_output(
    ctx: Ctx,
    sessions: AgentSessionsService,
    agent_session_id: UUID,
    tool_output_id: UUID,
    start: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1)] = 10_000,
) -> ToolOutputPageView:
    """A page of what the session keeps whole outside its window, from the
    character at `start`: a tool output is written once, so no character's
    place moves between pages. The policy's page bounds `limit`."""
    return await sessions.get_tool_output(ctx, agent_session_id, tool_output_id, start, limit)


@operator.get(
    "/orgs/{org_id}/agent-sessions/{agent_session_id}/usage", response_model=AgentSessionUsageView
)
async def get_agent_session_usage(
    admin: OperatorCtx,
    service: AgentSessionsService,
    org_id: UUID,
    agent_session_id: UUID,
    cursor: str | None = None,
    limit: int = LIMIT_DEFAULT,
) -> AgentSessionUsageView:
    """What one session's model calls used and cost, in every storage mode
    (ADR 1014). Requires the read permission.

    The answer is a `AgentSessionUsageView`: `agent_session_id`; `items`, a page of the
    session's usage records, oldest first, each with `id`, `created_at`,
    `spend_hold_id`, `agent_session_id`, `tree_id`, `loop_id`, `agent_step_id` (the call's
    response step), `agent_kind`, `kind_version`, `role`, `provider`, `model`, `input_tokens`,
    `cache_read_tokens`, `cache_write_tokens`, `output_tokens`,
    `thinking_tokens`, `cost_micros` (reference cost in millionths, null when
    no price applied), `latency_ms`, and `settled_whole` (a call whose usage
    was never reported whole, counted at its whole hold, its tokens partial
    at most); `next_cursor`, the next page's `cursor`, or null; `loops`, one
    `{loop_id, rollup}` per loop in the order it first called;
    `has_more_loops`; and `total`, the session's rollup. A rollup holds
    `calls`, the five token classes, `cost_micros`, `unpriced` (calls no
    price applied to, whose cost no figure holds), `settled_whole` (calls
    counted at their whole hold), and `latency_ms`. The rollups cover every
    record, whatever the page.

    A record holds ids, counts, money, a duration, and labels, and no
    content. `404 not_found` for an unknown org, and for a session the org
    holds no record of, which is how another tenant's session reads."""
    return await service.get_agent_session_usage(admin, org_id, agent_session_id, cursor, limit)
