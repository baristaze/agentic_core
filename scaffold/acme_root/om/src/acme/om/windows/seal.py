"""The seal an artifact's text takes before it reaches the object store. An
artifact is content, like the step it came from, so it is sealed under its
session's key: a reader of the store sees noise, revoking the key erases it,
and the record of it stays. The key service holds the key; this is the
narrow face of it the windows read, and a root wires the privacy
namespace's seal behind it."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.base import Platform
from acme.om.context import TenantContext


class SealedArtifact(Platform):
    """An artifact's text, sealed, and whether its session keeps content at
    rest: when it does not, the blob is held in the runtime's memory alone,
    never in the store."""

    blob: bytes
    at_rest: bool


class ArtifactSealInterface(ABC):
    @abstractmethod
    async def seal(
        self, ctx: TenantContext, session_id: UUID, artifact_id: UUID, data: bytes
    ) -> SealedArtifact:
        """`data` sealed under the current version of the session's key,
        bound to the tenant, the session, the artifact, and the version, so
        a blob copied to another artifact opens nothing. A session that
        keeps no content at rest seals its artifact all the same, and holds
        it in memory, so revoking its key erases it there too. `KeyRevoked`
        when the key is revoked."""
        ...

    @abstractmethod
    async def open(
        self, ctx: TenantContext, session_id: UUID, artifact_id: UUID, sealed: bytes
    ) -> bytes | None:
        """The plain bytes of a blob `seal` made for this artifact; None when
        the version it names is destroyed, and the content with it. A blob no
        seal of this platform made is refused, never read as anything."""
        ...
