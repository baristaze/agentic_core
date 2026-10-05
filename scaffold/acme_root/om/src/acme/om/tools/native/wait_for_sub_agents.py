"""`wait_for_sub_agents`: the agent waits for its sub-agents. The call
answers which of the session's children have a loop open; the loop then
parks on `children` before any further model call (`agents.loop_rules.
children_wait`), and a child's report that wakes the parent clears the
park (`agents.rules.CHILDREN_PARK`). With no child running there is
nothing to wait for, and the call is refused, so a loop never parks on a
report that cannot come. Past the tree's deadline none can come either,
and the loop parks on the deadline instead."""

from datetime import timedelta
from uuid import UUID

from acme.om.agent_sessions import AgentSessionsManagerInterface
from acme.om.agent_sessions.types.agent_session import AgentSession, SessionStatus
from acme.om.base import Platform
from acme.om.context import TenantContext
from acme.om.exceptions import ToolFailed
from acme.om.steps.types.header import ToolFailure
from acme.om.tools.tool import ToolInterface, ToolRuntime
from acme.om.tools.types.policy import Target
from acme.om.tools.types.tool import Effect, ToolClass, ToolInput, ToolMode, ToolSpec

WAIT_FOR_SUB_AGENTS = "wait_for_sub_agents"
CHILDREN_PAGE = 50

DESCRIPTION = (
    "Wait for your sub-agents. Your loop waits, holding nothing, until one of "
    "them reports, and its report arrives as a message you go on from. Call "
    "it once you have nothing left to do but wait. With no sub-agent running "
    "it is refused: every one that ended has reported already."
)


class WaitForSubAgentsInput(ToolInput):
    """No input: the call waits on the session's own children."""


class SubAgent(Platform):
    session_id: UUID
    title: str
    kind: str
    status: SessionStatus


class Waiting(Platform):
    """The sub-agents the loop waits on: the session's children whose loop
    is open."""

    sub_agents: tuple[SubAgent, ...]


class WaitForSubAgentsToolImpl(ToolInterface):
    """Reads the children of the session that made the call, never of one
    its input names, and acts on nothing: what the loop does with the
    answer is park."""

    def __init__(self, sessions: AgentSessionsManagerInterface) -> None:
        self._sessions = sessions
        self._spec = ToolSpec(
            name=WAIT_FOR_SUB_AGENTS,
            description=DESCRIPTION,
            input_model=WaitForSubAgentsInput,
            output_model=Waiting,
            timeout=timedelta(seconds=10),
            authorization_class=ToolClass.READ,
            effect=Effect.READ_ONLY,
            interruptible=True,
            mode=ToolMode.SYNC,
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
        assert isinstance(call_input, WaitForSubAgentsInput)
        running = [
            child
            for child in await self._children(ctx, runtime.session_id)
            if child.status is not SessionStatus.IDLE
        ]
        if not running:
            raise ToolFailed(
                ToolFailure.PERMANENT,
                "no sub-agent of yours is running, so there is nothing to wait for: "
                "each one that ended has reported to you already",
            )
        return Waiting(
            sub_agents=tuple(
                SubAgent(
                    session_id=child.id, title=child.title, kind=child.kind, status=child.status
                )
                for child in running
            )
        )

    async def _children(self, ctx: TenantContext, session_id: UUID) -> list[AgentSession]:
        """Every child of the session, a page at a time; its tree's count
        bounds them."""
        children: list[AgentSession] = []
        after: UUID | None = None
        while True:
            page = await self._sessions.get_children(ctx, session_id, after, CHILDREN_PAGE)
            children.extend(page.items)
            if not page.has_more or not page.items:
                return children
            after = page.items[-1].id
