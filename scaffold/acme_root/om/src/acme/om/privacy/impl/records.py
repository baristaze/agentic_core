"""The seal of a command's output in its transport's record, under its
session's key, as an artifact's text is sealed on its way to the store.

The output is sealed under the current version of the session's key, in
the artifacts' form (`blobs`), bound to the tenant, the session, the
command, and the version, so a record copied to another command opens
nothing. Once the version is destroyed, the output is noise, and the
record still says how the command ended. A session that keeps no content
at rest, or whose key is revoked, has its record keep the outcome alone."""

from uuid import UUID

from acme.infra.transports import RecordSealInterface
from acme.om.exceptions import KeyRevoked
from acme.om.privacy.impl.blobs import VERSION_BYTES, blob_version, open_blob, seal_blob
from acme.om.privacy.keys import SessionKeysInterface
from acme.om.privacy.storage import PrivacyStorageInterface
from acme.om.privacy.types.session_privacy import StorageMode


def bound_to(org_id: UUID, session_id: UUID, key: UUID, version: int) -> bytes:
    """What a sealed record is bound to: the tenant, the session, the
    command's key, and the version of the session's key."""
    return (
        b"command record"
        + org_id.bytes
        + session_id.bytes
        + key.bytes
        + version.to_bytes(VERSION_BYTES, "big")
    )


class RecordSealKeysImpl(RecordSealInterface):
    """The seal one session's commands go to their transport with."""

    def __init__(
        self,
        keys: SessionKeysInterface,
        policies: PrivacyStorageInterface,
        org_id: UUID,
        session_id: UUID,
    ) -> None:
        self._keys = keys
        self._policies = policies
        self._org_id = org_id
        self._session_id = session_id

    async def seal(self, key: UUID, data: bytes) -> bytes | None:
        record = await self._policies.read_privacy(self._org_id, self._session_id)
        if record is not None and record.policy.mode is StorageMode.MEMORY_ONLY:
            return None
        try:
            current = await self._keys.current(self._org_id, self._session_id)
        except KeyRevoked:
            return None
        return seal_blob(current, data, lambda version: self._bound(key, version))

    async def open(self, key: UUID, sealed: bytes) -> bytes | None:
        what = f"the record of command {key}"
        version = blob_version(sealed, what)
        keys = await self._keys.opened(self._org_id, self._session_id, {version})
        found = keys.get(version)
        if found is None:
            return None
        return open_blob(found, sealed, lambda version: self._bound(key, version), what)

    def _bound(self, key: UUID, version: int) -> bytes:
        return bound_to(self._org_id, self._session_id, key, version)
