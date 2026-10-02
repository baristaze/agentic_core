from collections.abc import Callable, Set
from datetime import datetime
from uuid import UUID

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from acme.infra.keys import KeyServiceInterface, WrappedKey
from acme.om.base import new_id, utcnow
from acme.om.exceptions import KeyRevoked
from acme.om.privacy.keys import OpenKey, SessionKeysInterface
from acme.om.privacy.storage import PrivacyStorageInterface
from acme.om.privacy.types.session_privacy import SessionKey, SessionPrivacy

HASH_KEY_INFO = b"keyed hash"
"""What tells the hash key apart from the data key it is derived from."""


class SessionKeysImpl(SessionKeysInterface):
    def __init__(
        self,
        storage: PrivacyStorageInterface,
        service: KeyServiceInterface,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._storage = storage
        self._service = service
        self._clock = clock

    async def current(self, org_id: UUID, session_id: UUID) -> OpenKey:
        ring = await self._storage.read_keys(org_id, session_id)
        if ring.revoked:
            raise KeyRevoked(f"the key of session {session_id} is revoked")
        key = ring.current()
        if key is None:
            return await self._made(org_id, session_id, 1)
        return OpenKey(version=key.version, key=await self._unwrap(org_id, key))

    async def opened(
        self, org_id: UUID, session_id: UUID, versions: Set[int]
    ) -> dict[int, bytes]:
        if not versions:
            return {}
        ring = await self._storage.read_keys(org_id, session_id)
        keys: dict[int, bytes] = {}
        for version in sorted(versions):
            key = ring.version(version)
            if key is not None and not key.is_destroyed():
                keys[version] = await self._unwrap(org_id, key)
        return keys

    async def add_version(self, org_id: UUID, session_id: UUID) -> int:
        ring = await self._storage.read_keys(org_id, session_id)
        if ring.revoked:
            raise KeyRevoked(f"the key of session {session_id} is revoked")
        latest = ring.current()
        made = await self._made(org_id, session_id, 1 if latest is None else latest.version + 1)
        return made.version

    async def hash_key(self, org_id: UUID, session_id: UUID) -> bytes:
        ring = await self._storage.read_keys(org_id, session_id)
        if ring.revoked:
            raise KeyRevoked(f"the key of session {session_id} is revoked")
        first = ring.version(1)
        if first is None:
            key = (await self._made(org_id, session_id, 1)).key
        else:
            key = await self._unwrap(org_id, first)
        return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=HASH_KEY_INFO).derive(
            key
        )

    async def rewrap(self, org_id: UUID, before: datetime, limit: int) -> int:
        now = self._clock()
        stale = await self._storage.read_keys_wrapped_before(org_id, min(before, now), limit)
        moved = 0
        for key in stale:
            if key.wrapped is None or key.wrapping is None:
                continue
            wrapped = await self._service.rewrap(
                org_id,
                key.session_id,
                key.version,
                WrappedKey(blob=key.wrapped, wrapping=key.wrapping),
            )
            rewrapped = key.model_copy(
                update={"wrapped": wrapped.blob, "wrapping": wrapped.wrapping, "wrapped_at": now}
            )
            if await self._storage.rewrap_key(org_id, rewrapped, key.wrapped):
                moved += 1
        return moved

    async def _made(self, org_id: UUID, session_id: UUID, version: int) -> OpenKey:
        """Version `version`, made, wrapped, and kept, under the session's
        record, which the default writes when the session has none. A call
        that made the same version first wins, and its key is the one
        answered."""
        now = self._clock()
        await self._storage.create_privacy(org_id, SessionPrivacy(id=session_id, created_at=now))
        data = await self._service.generate(org_id, session_id, version)
        made = SessionKey(
            id=new_id(),
            created_at=now,
            session_id=session_id,
            version=version,
            wrapped=data.wrapped.blob,
            wrapping=data.wrapped.wrapping,
            wrapped_at=now,
        )
        stored = await self._storage.add_key(org_id, made)
        if stored.id == made.id:
            return OpenKey(version=version, key=data.plaintext)
        return OpenKey(version=stored.version, key=await self._unwrap(org_id, stored))

    async def _unwrap(self, org_id: UUID, key: SessionKey) -> bytes:
        if key.wrapped is None or key.wrapping is None:
            raise KeyRevoked(f"version {key.version} of session {key.session_id} is destroyed")
        return await self._service.unwrap(
            org_id, key.session_id, key.version, WrappedKey(blob=key.wrapped, wrapping=key.wrapping)
        )
