from uuid import UUID

from acme.integrations.model_providers.types import Usage
from acme.om.agent_sessions import AgentSessionsManagerInterface
from acme.om.agent_sessions.types.agent_session import AgentSession, AgentSessionStatus, Among
from acme.om.agent_steps import AgentStepsManagerInterface
from acme.om.agent_steps.rules import control_step, message_step
from acme.om.agent_steps.types.agent_step import AgentStep, AgentStepType
from acme.om.agent_steps.types.content import TextBlock
from acme.om.agent_steps.types.header import (
    ControlCommand,
    ControlHeader,
    InputHeader,
    LoopEndedHeader,
    ModelResponseHeader,
    Park,
    ParkedHeader,
    SwitchedHeader,
    ToolRequestHeader,
    ToolResponseHeader,
    WorkspaceSnapshot,
)
from acme.om.agents import AgentsManagerInterface
from acme.om.agents.loop import LoopManagerInterface
from acme.om.agents.types.request import Start
from acme.om.attribution import AttributionManagerInterface
from acme.om.base import utcnow
from acme.om.context import TenantContext
from acme.om.exceptions import NotFound
from acme.om.tools import ToolsManagerInterface
from acme.om.tools.rules import named_snapshot
from acme.om.tools.types.call import JobCompletion
from acme.om.windows import WindowsManagerInterface
from acme.services.api.services.agent_sessions import AgentSessionsServiceInterface
from acme.services.api.types.agent_sessions import (
    AgentSessionPageView,
    AgentSessionView,
    AgentStepPageView,
    AgentStepUsageView,
    AgentStepView,
    ControlRequest,
    DeadlineRequest,
    DecisionRequest,
    GiveBackRequest,
    JobCompletionRequest,
    MessageRequest,
    ParkView,
    PrincipalRequest,
    SnapshotView,
    StartAgentSessionRequest,
    ToolOutputPageView,
    VisibilityRequest,
)
from acme.services.api.types.common import clamp_limit


def park_view(park: Park | None) -> ParkView | None:
    return None if park is None else ParkView.model_validate(park)


def agent_session_view(session: AgentSession) -> AgentSessionView:
    child = session.parent_id is not None
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
        visibility=session.visibility,
        participants=list(session.participants),
        steerer=None if child else session.steerer or session.created_by,
        parent_id=session.parent_id,
    )


def text_of(step: AgentStep) -> str:
    """What a step says: a tool response's text parts, any other step's
    text blocks. A step whose content is gone, its session's key revoked,
    says nothing: a hole in a known place, whose shape alone stays."""
    if not step.content.is_plain():
        return ""
    if step.type is AgentStepType.TOOL_RESPONSE:
        parts = step.as_tool_response().parts
        return "\n".join(part.text for part in parts if isinstance(part, TextBlock))
    return step.as_text()


def usage_view(usage: Usage | None) -> AgentStepUsageView | None:
    return None if usage is None else AgentStepUsageView.model_validate(usage.model_dump())


def step_view(step: AgentStep) -> AgentStepView:
    header = step.header
    responded = header if isinstance(header, ModelResponseHeader) else None
    return AgentStepView(
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
        usage=usage_view(responded.usage) if responded is not None else None,
        tool=header.tool if isinstance(header, ToolRequestHeader) else None,
        failure=header.failure if isinstance(header, ToolResponseHeader) else None,
        command=header.command if isinstance(header, ControlHeader) else None,
        park=park_view(header.park) if isinstance(header, ParkedHeader) else None,
        outcome=header.outcome if isinstance(header, LoopEndedHeader) else None,
        cause=header.cause if isinstance(header, LoopEndedHeader) else None,
        snapshot=snapshot_view(named_snapshot(step)),
        author=header.principal.id if isinstance(header, InputHeader) else None,
        participant=isinstance(header, InputHeader) and header.participant,
        steerer=header.steer.steerer.id
        if isinstance(header, SwitchedHeader) and header.steer
        else None,
        visibility=header.visibility.visibility
        if isinstance(header, SwitchedHeader) and header.visibility
        else None,
    )


def snapshot_view(snapshot: WorkspaceSnapshot | None) -> SnapshotView | None:
    return None if snapshot is None else SnapshotView.model_validate(snapshot.model_dump())


class AgentSessionsServiceImpl(AgentSessionsServiceInterface):
    def __init__(
        self,
        sessions: AgentSessionsManagerInterface,
        agents: AgentsManagerInterface,
        steps: AgentStepsManagerInterface,
        tools: ToolsManagerInterface,
        attribution: AttributionManagerInterface,
        loop: LoopManagerInterface,
        windows: WindowsManagerInterface,
    ) -> None:
        self._windows = windows
        self._sessions = sessions
        self._agents = agents
        self._steps = steps
        self._tools = tools
        self._attribution = attribution
        self._loop = loop

    async def start_agent_session(
        self, ctx: TenantContext, body: StartAgentSessionRequest, agent_session_id: UUID
    ) -> AgentSessionView:
        start = Start(id=agent_session_id, kind=body.kind, title=body.title)
        return agent_session_view(await self._agents.start_agent_session(ctx, start))

    async def get_agent_session(
        self, ctx: TenantContext, agent_session_id: UUID
    ) -> AgentSessionView:
        return agent_session_view(await self._sessions.get_agent_session(ctx, agent_session_id))

    async def list_sessions(
        self,
        ctx: TenantContext,
        among: Among,
        status: AgentSessionStatus | None,
        after: UUID | None,
        limit: int,
    ) -> AgentSessionPageView:
        page = await self._sessions.get_agent_sessions(
            ctx, status, after, clamp_limit(limit), among
        )
        return AgentSessionPageView(
            items=[agent_session_view(session) for session in page.items], has_more=page.has_more
        )

    async def add_participant(
        self, ctx: TenantContext, agent_session_id: UUID, user_id: UUID
    ) -> AgentSessionView:
        return agent_session_view(
            await self._sessions.add_participant(ctx, agent_session_id, user_id)
        )

    async def remove_participant(
        self, ctx: TenantContext, agent_session_id: UUID, user_id: UUID
    ) -> AgentSessionView:
        return agent_session_view(
            await self._sessions.remove_participant(ctx, agent_session_id, user_id)
        )

    async def switch_visibility(
        self, ctx: TenantContext, agent_session_id: UUID, body: VisibilityRequest
    ) -> AgentSessionView:
        switched = await self._sessions.switch_visibility(ctx, agent_session_id, body.visibility)
        return agent_session_view(switched)

    async def send_message(
        self, ctx: TenantContext, agent_session_id: UUID, body: MessageRequest, agent_step_id: UUID
    ) -> AgentStepView:
        message = message_step(agent_step_id, utcnow(), agent_session_id, ctx, body.text)
        (stored,), _ = await self._sessions.receive(ctx, agent_session_id, [message])
        return step_view(stored)

    async def send_control(
        self, ctx: TenantContext, agent_session_id: UUID, body: ControlRequest, agent_step_id: UUID
    ) -> AgentStepView:
        command = ControlCommand(body.command.value)
        call = None
        if body.request_seq is not None:
            call = (await self._tool_request(ctx, agent_session_id, body.request_seq)).id
        snapshot = None
        if body.snapshot_id is not None:
            # Read after the session itself, so another tenant's names
            # nothing, and found in this session's history alone, so no
            # session starts from another's snapshot.
            await self._sessions.get_agent_session(ctx, agent_session_id)
            snapshot = await self._tools.find_snapshot(ctx, agent_session_id, body.snapshot_id)
        control = control_step(
            agent_step_id, utcnow(), agent_session_id, ctx, command, call, snapshot
        )
        (stored,), _ = await self._sessions.receive(ctx, agent_session_id, [control])
        return step_view(stored)

    async def _tool_request(
        self, ctx: TenantContext, agent_session_id: UUID, seq: int
    ) -> AgentStep:
        """The tool request at `seq` in the session's history, read after the
        session itself, so another tenant's names nothing; `NotFound` when no
        tool request is there."""
        await self._sessions.get_agent_session(ctx, agent_session_id)
        page = await self._steps.get_agent_steps(ctx, agent_session_id, seq - 1, 1)
        found = next((step for step in page.items if step.seq == seq), None)
        if found is None or found.type is not AgentStepType.TOOL_REQUEST:
            raise NotFound(f"no tool request at {seq} in agent session {agent_session_id}")
        return found

    async def decide_call(
        self, ctx: TenantContext, agent_session_id: UUID, request_seq: int, body: DecisionRequest
    ) -> AgentStepView:
        # The session first: one another tenant holds, or one marked
        # deleted, takes no decision.
        await self._sessions.get_agent_session(ctx, agent_session_id)
        decided = await self._tools.decide_call(
            ctx, agent_session_id, request_seq, approve=body.approve, note=body.note
        )
        await self._sessions.project_status(ctx, agent_session_id)
        return step_view(decided)

    async def set_deadline(
        self, ctx: TenantContext, agent_session_id: UUID, body: DeadlineRequest
    ) -> AgentSessionView:
        await self._agents.set_deadline(ctx, agent_session_id, body.deadline)
        return agent_session_view(await self._sessions.get_agent_session(ctx, agent_session_id))

    async def assign_principal(
        self, ctx: TenantContext, agent_session_id: UUID, body: PrincipalRequest | None
    ) -> AgentSessionView:
        steerer = None if body is None else body.steerer
        await self._attribution.assign_principal(ctx, agent_session_id, steerer)
        return agent_session_view(await self._sessions.get_agent_session(ctx, agent_session_id))

    async def take_over(self, ctx: TenantContext, agent_session_id: UUID) -> AgentSessionView:
        return agent_session_view(await self._loop.take_over(ctx, agent_session_id))

    async def give_back(
        self, ctx: TenantContext, agent_session_id: UUID, body: GiveBackRequest
    ) -> AgentSessionView:
        return agent_session_view(await self._loop.give_back(ctx, agent_session_id, body.summary))

    async def complete_job(
        self, ctx: TenantContext, agent_session_id: UUID, key: UUID, body: JobCompletionRequest
    ) -> AgentSessionView:
        completion = JobCompletion(
            key=key, handle=body.handle, text=body.text, failure=body.failure
        )
        return agent_session_view(await self._loop.complete_job(ctx, agent_session_id, completion))

    async def get_agent_steps(
        self, ctx: TenantContext, agent_session_id: UUID, after_seq: int, limit: int
    ) -> AgentStepPageView:
        await self._sessions.get_agent_session(ctx, agent_session_id)
        page = await self._steps.get_agent_steps(
            ctx, agent_session_id, after_seq, clamp_limit(limit)
        )
        return AgentStepPageView(
            items=[step_view(step) for step in page.items], has_more=page.has_more
        )

    async def get_tool_output(
        self,
        ctx: TenantContext,
        agent_session_id: UUID,
        tool_output_id: UUID,
        start: int,
        limit: int,
    ) -> ToolOutputPageView:
        # The session first, so one marked deleted keeps its tool outputs closed
        # as it keeps its history.
        await self._sessions.get_agent_session(ctx, agent_session_id)
        page = await self._windows.get_tool_output(
            ctx, agent_session_id, tool_output_id, start, limit
        )
        return ToolOutputPageView(
            tool_output_id=page.tool_output_id,
            start=page.offset,
            text=page.text,
            characters=page.characters,
            has_more=page.has_more,
        )
