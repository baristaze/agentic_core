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


class SnapshotSealInterface(ABC):
    """The seal a workspace's snapshot takes before it reaches the object
    store. What a workspace holds is content, like the steps its commands
    answered, so its snapshot is sealed under its session's key: a reader
    of the store sees noise, revoking the key erases it, and the step that
    names it stays."""

    @abstractmethod
    async def keeps(self, ctx: TenantContext, session_id: UUID) -> bool:
        """Whether the session keeps content at rest; `seal` answers None
        for one that does not. Asked before a snapshot is taken, so what a
        workspace holds never leaves a session that keeps no content at
        rest, not even as a sub-agent's copy."""
        ...

    @abstractmethod
    async def seal(
        self, ctx: TenantContext, session_id: UUID, digest: str, data: bytes
    ) -> bytes | None:
        """`data` sealed under the current version of the session's key,
        bound to the tenant, the session, the snapshot's hash `digest`, and
        the version, so a blob copied to another session or another hash
        opens nothing. None when the session keeps no content at rest: no
        snapshot of it is kept. `KeyRevoked` when its key is revoked."""
        ...

    @abstractmethod
    async def open(
        self, ctx: TenantContext, session_id: UUID, digest: str, sealed: bytes
    ) -> bytes | None:
        """The plain bytes of a blob `seal` made for this hash of this
        session; None when the version it names is destroyed, and the
        snapshot with it. A blob no seal of this platform made for it, one
        whose bytes were altered included, is refused (`ValueError`), never
        read as anything."""
        ...
