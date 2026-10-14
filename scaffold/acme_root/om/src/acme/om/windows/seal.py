"""The seal a tool output's text takes before it reaches the object store. A
tool output is content, like the step it came from, so it is sealed under its
session's key: a reader of the store sees noise, revoking the key erases it,
and the record of it stays. The key service holds the key; this is the
narrow face of it the windows read, and a root wires the privacy
namespace's seal behind it."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.base import Platform
from acme.om.context import TenantContext


class SealedToolOutput(Platform):
    """A tool output's text, sealed, and whether its session keeps content at
    rest: when it does not, the blob is held in the runtime's memory alone,
    never in the store."""

    blob: bytes
    at_rest: bool


class ToolOutputSealInterface(ABC):
    @abstractmethod
    async def seal(
        self, ctx: TenantContext, agent_session_id: UUID, tool_output_id: UUID, data: bytes
    ) -> SealedToolOutput:
        """`data` sealed under the current version of the session's key,
        bound to the tenant, the session, the tool output, and the version, so
        a blob copied to another tool output opens nothing. A session that
        keeps no content at rest seals its tool output all the same, and holds
        it in memory, so revoking its key erases it there too. `KeyRevoked`
        when the key is revoked."""
        ...

    @abstractmethod
    async def open(
        self, ctx: TenantContext, agent_session_id: UUID, tool_output_id: UUID, sealed: bytes
    ) -> bytes | None:
        """The plain bytes of a blob `seal` made for this tool output; None when
        the version it names is destroyed, and the content with it. A blob no
        seal of this platform made is refused, never read as anything."""
        ...
