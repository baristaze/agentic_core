from collections.abc import Sequence

from acme.infra.base import QuietNull
from acme.om.agent_sessions.types.agent_session import AgentSession
from acme.om.agent_steps.types.agent_step import AgentStep
from acme.om.agents.hooks import (
    NO_ATTACHED_TOOLSETS,
    AttachedToolsets,
    LoopHooksInterface,
    LoopStart,
)
from acme.om.context import TenantContext


class LoopHooksNullImpl(LoopHooksInterface, QuietNull):
    """The hooks of a root that wired none: every session holds its kind's
    tools, and nothing is appended before a run's first call."""

    async def attached_toolsets(
        self, ctx: TenantContext, agent_session: AgentSession
    ) -> AttachedToolsets:
        return NO_ATTACHED_TOOLSETS

    async def before_first_call(self, ctx: TenantContext, start: LoopStart) -> Sequence[AgentStep]:
        return ()
