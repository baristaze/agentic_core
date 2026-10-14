from uuid import UUID

from acme.om.context import TenantContext
from acme.om.exceptions import Unavailable
from acme.om.windows.seal import SealedToolOutput, ToolOutputSealInterface


class ToolOutputSealNullImpl(ToolOutputSealInterface):
    """The seal of a root that wired no key service. It is loud: a tool output
    kept in the clear would outlive the revocation that erases its session's
    content, so it refuses and says why."""

    async def seal(
        self, ctx: TenantContext, agent_session_id: UUID, tool_output_id: UUID, data: bytes
    ) -> SealedToolOutput:
        raise Unavailable("no key service is wired, so no tool output is kept")

    async def open(
        self, ctx: TenantContext, agent_session_id: UUID, tool_output_id: UUID, sealed: bytes
    ) -> bytes | None:
        raise Unavailable("no key service is wired, so no tool output is opened")
