"""The seal a workspace's snapshot takes on its way to the object store, under
its session's key, as an artifact's text is sealed on its way there.

The snapshot is sealed under the current version of the session's key, in
the artifacts' form (`blobs`), bound to the tenant, the session, the
snapshot's hash, and the version, so a blob copied to another session or
put under another hash opens nothing, and one whose bytes were altered is
refused. Once the version is destroyed, the snapshot is noise, and the step
that names it stays. A session that keeps no content at rest keeps no
snapshot."""

from uuid import UUID

from acme.om.context import TenantContext
from acme.om.privacy.impl.blobs import VERSION_BYTES, blob_version, open_blob, seal_blob
from acme.om.privacy.keys import SessionKeysInterface
from acme.om.privacy.storage import PrivacyStorageInterface
from acme.om.privacy.types.session_privacy import StorageMode
from acme.om.tools.seal import SnapshotSealInterface


def bound_to(org_id: UUID, session_id: UUID, digest: str, version: int) -> bytes:
    """What a sealed snapshot is bound to: the tenant, the session, the
    snapshot's hash, and the version of the session's key."""
    return (
        b"workspace snapshot"
        + org_id.bytes
        + session_id.bytes
        + digest.encode()
        + version.to_bytes(VERSION_BYTES, "big")
    )


class SnapshotSealKeysImpl(SnapshotSealInterface):
    def __init__(self, keys: SessionKeysInterface, policies: PrivacyStorageInterface) -> None:
        self._keys = keys
        self._policies = policies

    async def keeps(self, ctx: TenantContext, session_id: UUID) -> bool:
        record = await self._policies.read_privacy(ctx.org_id, session_id)
        return record is None or record.policy.mode is not StorageMode.MEMORY_ONLY

    async def seal(
        self, ctx: TenantContext, session_id: UUID, digest: str, data: bytes
    ) -> bytes | None:
        if not await self.keeps(ctx, session_id):
            return None
        current = await self._keys.current(ctx.org_id, session_id)
        return seal_blob(
            current, data, lambda version: bound_to(ctx.org_id, session_id, digest, version)
        )

    async def open(
        self, ctx: TenantContext, session_id: UUID, digest: str, sealed: bytes
    ) -> bytes | None:
        what = f"the snapshot {digest}"
        version = blob_version(sealed, what)
        keys = await self._keys.opened(ctx.org_id, session_id, {version})
        found = keys.get(version)
        if found is None:
            return None
        return open_blob(
            found, sealed, lambda version: bound_to(ctx.org_id, session_id, digest, version), what
        )
