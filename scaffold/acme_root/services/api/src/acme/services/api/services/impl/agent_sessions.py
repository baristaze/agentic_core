from uuid import UUID

from acme.om.agent_sessions import AgentSessionsManagerInterface
from acme.om.agent_sessions.types.agent_session import AgentSession
from acme.om.agents import AgentsManagerInterface
from acme.om.agents.types.request import Start
from acme.om.base import utcnow
from acme.om.context import TenantContext
from acme.om.exceptions import NotFound
from acme.om.steps import StepsManagerInterface
from acme.om.steps.rules import control_step, message_step
from acme.om.steps.types.content import TextBlock
from acme.om.steps.types.header import (
    ControlCommand,
    ControlHeader,
    LoopEndedHeader,
    ModelResponseHeader,
    Park,
    ParkedHeader,
    ToolRequestHeader,
    ToolResponseHeader,
)
from acme.om.steps.types.step import Step, StepType
from acme.om.tools import ToolsManagerInterface
from acme.services.api.services.agent_sessions import AgentSessionsServiceInterface
from acme.services.api.types.agent_sessions import (
    AgentSessionView,
    ControlRequest,
    DecisionRequest,
    MessageRequest,
    ParkView,
    StartSessionRequest,
    StepPageView,
    StepView,
)
from acme.services.api.types.common import clamp_limit


def park_view(park: Park | None) -> ParkView | None:
    return None if park is None else ParkView.model_validate(park)


def session_view(session: AgentSession) -> AgentSessionView:
    return AgentSessionView(
        id=session.id,
        title=session.title,
        kind=session.kind,
        kind_version=session.kind_version,
        status=session.status,
        park=park_view(session.park),
        created_at=session.created_at,
        created_by=session.created_by,
        archived_at=session.archived_at,
    )


def text_of(step: Step) -> str:
    """What a step says: a tool response's text parts, any other step's
    text blocks."""
    if step.type is StepType.TOOL_RESPONSE:
        parts = step.as_tool_response().parts
        return "\n".join(part.text for part in parts if isinstance(part, TextBlock))
    return step.as_text()


def step_view(step: Step) -> StepView:
    header = step.header
    responded = header if isinstance(header, ModelResponseHeader) else None
    return StepView(
        id=step.id,
        seq=step.seq,
        loop_id=step.loop_id,
        type=step.type,
        actor=step.actor,
        origin=step.origin,
        responds_to=step.responds_to,
        refs=list(step.refs),
        created_at=step.created_at,
        text=text_of(step),
        tools=[use.name for use in step.as_tool_uses()] if responded is not None else [],
        stop_reason=None if responded is None else responded.stop_reason,
        tool=header.tool if isinstance(header, ToolRequestHeader) else None,
        failure=header.failure if isinstance(header, ToolResponseHeader) else None,
        command=header.command if isinstance(header, ControlHeader) else None,
        park=park_view(header.park) if isinstance(header, ParkedHeader) else None,
        outcome=header.outcome if isinstance(header, LoopEndedHeader) else None,
    )


class AgentSessionsServiceImpl(AgentSessionsServiceInterface):
    def __init__(
        self,
        sessions: AgentSessionsManagerInterface,
        agents: AgentsManagerInterface,
        steps: StepsManagerInterface,
        tools: ToolsManagerInterface,
    ) -> None:
        self._sessions = sessions
        self._agents = agents
        self._steps = steps
        self._tools = tools

    async def start_session(
        self, ctx: TenantContext, body: StartSessionRequest, session_id: UUID
    ) -> AgentSessionView:
        start = Start(id=session_id, kind=body.kind, title=body.title)
        return session_view(await self._agents.start_session(ctx, start))

    async def get_session(self, ctx: TenantContext, session_id: UUID) -> AgentSessionView:
        return session_view(await self._sessions.get_session(ctx, session_id))

    async def send_message(
        self, ctx: TenantContext, session_id: UUID, body: MessageRequest, step_id: UUID
    ) -> StepView:
        message = message_step(step_id, utcnow(), session_id, ctx, body.text)
        (stored,), _ = await self._sessions.receive(ctx, session_id, [message])
        return step_view(stored)

    async def send_control(
        self, ctx: TenantContext, session_id: UUID, body: ControlRequest, step_id: UUID
    ) -> StepView:
        command = ControlCommand(body.command.value)
        call = None
        if body.request_seq is not None:
            call = (await self._tool_request(ctx, session_id, body.request_seq)).id
        control = control_step(step_id, utcnow(), session_id, ctx, command, call)
        (stored,), _ = await self._sessions.receive(ctx, session_id, [control])
        return step_view(stored)

    async def _tool_request(self, ctx: TenantContext, session_id: UUID, seq: int) -> Step:
        """The tool request at `seq` in the session's history, read after the
        session itself, so another tenant's names nothing; `NotFound` when no
        tool request is there."""
        await self._sessions.get_session(ctx, session_id)
        page = await self._steps.get_steps(ctx, session_id, seq - 1, 1)
        found = next((step for step in page.items if step.seq == seq), None)
        if found is None or found.type is not StepType.TOOL_REQUEST:
            raise NotFound(f"no tool request at {seq} in agent session {session_id}")
        return found

    async def decide_call(
        self, ctx: TenantContext, session_id: UUID, request_seq: int, body: DecisionRequest
    ) -> StepView:
        # The session first: one another tenant holds, or one marked
        # deleted, takes no decision.
        await self._sessions.get_session(ctx, session_id)
        decided = await self._tools.decide_call(
            ctx, session_id, request_seq, approve=body.approve, note=body.note
        )
        await self._sessions.project_status(ctx, session_id)
        return step_view(decided)

    async def get_steps(
        self, ctx: TenantContext, session_id: UUID, after_seq: int, limit: int
    ) -> StepPageView:
        await self._sessions.get_session(ctx, session_id)
        page = await self._steps.get_steps(ctx, session_id, after_seq, clamp_limit(limit))
        return StepPageView(items=[step_view(step) for step in page.items], has_more=page.has_more)
