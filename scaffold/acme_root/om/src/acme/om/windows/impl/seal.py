from uuid import UUID

from acme.om.context import TenantContext
from acme.om.exceptions import Unavailable
from acme.om.windows.seal import ArtifactSealInterface, SealedArtifact


class ArtifactSealNullImpl(ArtifactSealInterface):
    """The seal of a root that wired no key service. It is loud: an artifact
    kept in the clear would outlive the revocation that erases its session's
    content, so it refuses and says why."""

    async def seal(
        self, ctx: TenantContext, session_id: UUID, artifact_id: UUID, data: bytes
    ) -> SealedArtifact:
        raise Unavailable("no key service is wired, so no artifact is kept")

    async def open(
        self, ctx: TenantContext, session_id: UUID, artifact_id: UUID, sealed: bytes
    ) -> bytes | None:
        raise Unavailable("no key service is wired, so no artifact is opened")
