"""The seal a command's output takes in its transport's record. The transport
keeps that record so a new run can read how an unsafe call ended after a
crash; what the command printed is content, like the step it answers, so it
is sealed under its session's key: a reader of the host sees noise, revoking
the key erases it, and how the command ended stays readable. The key
service holds the key; this is the narrow face of it the tools read, and a
root wires the privacy namespace's seal behind it."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.context import TenantContext


class RecordSealInterface(ABC):
    @abstractmethod
    async def seal(
        self, ctx: TenantContext, session_id: UUID, key: UUID, data: bytes
    ) -> bytes | None:
        """`data` sealed under the current version of the session's key,
        bound to the tenant, the session, the command under `key`, and the
        version, so a blob copied to another command opens nothing. None
        when the session keeps no content at rest: it is memory-only, or its
        key is revoked, and the record keeps how the command ended alone."""
        ...

    @abstractmethod
    async def open(
        self, ctx: TenantContext, session_id: UUID, key: UUID, sealed: bytes
    ) -> bytes | None:
        """The plain bytes of a blob `seal` made for this command; None when
        the version it names is destroyed, and the output with it. A blob no
        seal of this platform made for this command is refused, never read as
        anything."""
        ...
