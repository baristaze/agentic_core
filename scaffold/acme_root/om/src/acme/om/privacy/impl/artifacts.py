"""The seal an artifact's text takes on its way to the object store, under
its session's key, as a step's content is sealed on its way into storage.

The text is sealed under the current version of the session's key with
AES-GCM, bound to the tenant, the session, the artifact, and the version,
so a blob copied to another artifact opens nothing. The blob carries the
version it was sealed under, and opens with that version alone: once the
version is destroyed, the blob is noise and the artifact's record stays. A
session that keeps no content at rest has its artifact sealed the same
way, and held in the runtime's memory instead of the store, so revoking
its key erases it there too."""

from uuid import UUID

from acme.om.context import TenantContext
from acme.om.privacy.impl.blobs import VERSION_BYTES, blob_version, open_blob, seal_blob
from acme.om.privacy.keys import SessionKeysInterface
from acme.om.privacy.storage import PrivacyStorageInterface
from acme.om.privacy.types.session_privacy import StorageMode
from acme.om.windows.seal import ArtifactSealInterface, SealedArtifact


def bound_to(org_id: UUID, session_id: UUID, artifact_id: UUID, version: int) -> bytes:
    """What a sealed artifact is bound to: the tenant, the session, the
    artifact, and the version of the key."""
    return (
        b"artifact content"
        + org_id.bytes
        + session_id.bytes
        + artifact_id.bytes
        + version.to_bytes(VERSION_BYTES, "big")
    )


class ArtifactSealKeysImpl(ArtifactSealInterface):
    def __init__(self, keys: SessionKeysInterface, policies: PrivacyStorageInterface) -> None:
        self._keys = keys
        self._policies = policies

    async def seal(
        self, ctx: TenantContext, session_id: UUID, artifact_id: UUID, data: bytes
    ) -> SealedArtifact:
        record = await self._policies.read_privacy(ctx.org_id, session_id)
        at_rest = record is None or record.policy.mode is not StorageMode.MEMORY_ONLY
        current = await self._keys.current(ctx.org_id, session_id)
        blob = seal_blob(
            current, data, lambda version: bound_to(ctx.org_id, session_id, artifact_id, version)
        )
        return SealedArtifact(blob=blob, at_rest=at_rest)

    async def open(
        self, ctx: TenantContext, session_id: UUID, artifact_id: UUID, sealed: bytes
    ) -> bytes | None:
        what = f"artifact {artifact_id}"
        version = blob_version(sealed, what)
        keys = await self._keys.opened(ctx.org_id, session_id, {version})
        key = keys.get(version)
        if key is None:
            return None
        return open_blob(
            key,
            sealed,
            lambda version: bound_to(ctx.org_id, session_id, artifact_id, version),
            what,
        )
