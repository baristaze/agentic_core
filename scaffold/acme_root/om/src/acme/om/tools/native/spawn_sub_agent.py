"""`spawn_sub_agent`: the agent starts a sub-agent, a session one level
down its tree that works on one part of its task in a clean context and
reports back (`agents.manager.AgentsManagerInterface.spawn`). The child's
id is the call's own, the id of its request, so a call asked again after a
lost run answers the child it made and starts no second one. A bound the
spawn reaches is the call's failure, which the model reads and can act on:
the tree's height or count, a kind with no share, a kind unknown or one
whose calls its sender may not make. The tree's deadline bounds the call
as it bounds every call of the tree."""

from collections.abc import Callable
from datetime import timedelta
from uuid import UUID

from pydantic import Field

from acme.om.agent_sessions import AgentSessionsManagerInterface
from acme.om.agents.manager import AgentsManagerInterface
from acme.om.agents.types.request import MAX_OBJECTIVE, MAX_TITLE, Spawn
from acme.om.attribution.types.principal import MAX_KIND
from acme.om.base import Platform
from acme.om.context import TenantContext
from acme.om.exceptions import (
    NotAuthorized,
    ToolFailed,
    TreeBoundReached,
    UnknownAgentKind,
    ValidationFailed,
)
from acme.om.steps.types.header import ToolFailure
from acme.om.tools.tool import ToolInterface, ToolRuntime
from acme.om.tools.types.policy import Target
from acme.om.tools.types.tool import Effect, ToolClass, ToolInput, ToolMode, ToolSpec

SPAWN_SUB_AGENT = "spawn_sub_agent"

DESCRIPTION = (
    "Start a sub-agent: a session below yours that works on one part of your "
    "task in a clean context. It never reads your history, so write an "
    "objective that stands on its own: what to do, the constraints that bind "
    "it, its bounds, and the shape of the report you want back. It spends "
    "from your task's budget and shares its deadline. `kind` is the kind of "
    "agent to start, your own when you leave it out. Its report arrives as a "
    "message when it ends or needs a person; call wait_for_sub_agents to wait "
    "for it."
)


class SpawnSubAgentInput(ToolInput):
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    objective: str = Field(min_length=1, max_length=MAX_OBJECTIVE)
    kind: str | None = Field(default=None, min_length=1, max_length=MAX_KIND)


class Spawned(Platform):
    """The sub-agent the call started, the same one however often the call
    is asked."""

    session_id: UUID
    title: str
    kind: str


class SpawnSubAgentToolImpl(ToolInterface):
    """A spawn through the agents manager, run under the call's context and
    for the session that made the call. `agents` is a provider and not the
    manager itself: the agents manager classes every tool of the catalog,
    this one among them, so it is built after the catalog and this edge is
    bound at call time."""

    def __init__(
        self,
        sessions: AgentSessionsManagerInterface,
        agents: Callable[[], AgentsManagerInterface],
    ) -> None:
        self._sessions = sessions
        self._agents = agents
        self._spec = ToolSpec(
            name=SPAWN_SUB_AGENT,
            description=DESCRIPTION,
            input_model=SpawnSubAgentInput,
            output_model=Spawned,
            timeout=timedelta(seconds=30),
            authorization_class=ToolClass.SPAWN,
            # A repeat under the call's id answers the child it made.
            effect=Effect.IDEMPOTENT,
            # A spawn cut off part way leaves a child no objective has woken;
            # only a repeat of the same call finishes it.
            interruptible=False,
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
        assert isinstance(call_input, SpawnSubAgentInput)
        kind = call_input.kind
        if kind is None:
            kind = (await self._sessions.get_session(ctx, runtime.session_id)).kind
        asked = Spawn(
            id=runtime.key, kind=kind, title=call_input.title, objective=call_input.objective
        )
        try:
            child = await self._agents().spawn(ctx, runtime.session_id, asked)
        except NotAuthorized as refused:
            raise ToolFailed(ToolFailure.DENIED, refused.message) from refused
        except (TreeBoundReached, UnknownAgentKind, ValidationFailed) as refused:
            raise ToolFailed(ToolFailure.PERMANENT, refused.message) from refused
        return Spawned(session_id=child.id, title=child.title, kind=child.kind)
