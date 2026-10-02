from datetime import datetime
from uuid import UUID

from acme.om.exceptions import KeyRevoked, NotFound, TenantMismatch, UniqueKeyTaken
from acme.om.outbox.storage import OutboxLandingInterface
from acme.om.outbox.types.row import OutboxRow
from acme.om.privacy.storage import PrivacyStorageInterface
from acme.om.privacy.types.session_privacy import KeyRing, SessionKey, SessionPrivacy
from acme.om.storage.impl.memory_base import MemoryStorageBase, MemoryTable


class PrivacyStorageMemoryImpl(MemoryStorageBase, PrivacyStorageInterface):
    def __init__(self, outbox: OutboxLandingInterface | None = None) -> None:
        super().__init__(outbox)
        self._records: MemoryTable[SessionPrivacy] = {}
        self._keys: MemoryTable[SessionKey] = {}

    async def create_privacy(
        self, org_id: UUID, record: SessionPrivacy, outbox_rows: tuple[OutboxRow, ...] = ()
    ) -> SessionPrivacy:
        async with self._lock:
            return self._created(org_id, record, outbox_rows)

    def _created(
        self, org_id: UUID, record: SessionPrivacy, outbox_rows: tuple[OutboxRow, ...]
    ) -> SessionPrivacy:
        if not self._insert(self._records, org_id, record, outbox_rows):
            stored = self._get(self._records, org_id, record.id)
            if stored is None:
                raise TenantMismatch(f"session privacy {record.id} is not in {org_id}")
            return stored
        return record

    async def read_privacy(self, org_id: UUID, session_id: UUID) -> SessionPrivacy | None:
        return self._get(self._records, org_id, session_id)

    async def add_key(self, org_id: UUID, key: SessionKey) -> SessionKey:
        async with self._lock:
            record = self._get(self._records, org_id, key.session_id)
            if record is None:
                raise NotFound(f"no privacy record for session {key.session_id}")
            if record.revoked_at is not None:
                raise KeyRevoked(f"the key of session {key.session_id} is revoked")
            stored = self._version(org_id, key.session_id, key.version)
            if stored is not None:
                return stored
            if key.id in self._keys:
                if self._keys[key.id][0] != org_id:
                    raise TenantMismatch(f"session key {key.id} is not in {org_id}")
                raise UniqueKeyTaken(f"session key {key.id} is another version's")
            self._put(self._keys, org_id, key)
            return key

    def _version(self, org_id: UUID, session_id: UUID, version: int) -> SessionKey | None:
        return next(
            (
                key
                for key in self._rows(self._keys, org_id)
                if key.session_id == session_id and key.version == version
            ),
            None,
        )

    async def read_keys(self, org_id: UUID, session_id: UUID) -> KeyRing:
        record = self._get(self._records, org_id, session_id)
        keys = sorted(
            (key for key in self._rows(self._keys, org_id) if key.session_id == session_id),
            key=lambda key: key.version,
        )
        return KeyRing(
            revoked=record is not None and record.revoked_at is not None, keys=tuple(keys)
        )

    async def revoke(
        self, org_id: UUID, record: SessionPrivacy, outbox_rows: tuple[OutboxRow, ...] = ()
    ) -> SessionPrivacy:
        at = record.revoked_at
        if at is None:
            raise ValueError("a revocation names when it happened")
        async with self._lock:
            stored = self._created(org_id, record, outbox_rows)
            if stored.revoked_at is not None and stored is not record:
                return stored
            if stored is not record:
                stored = stored.model_copy(
                    update={"revoked_at": at, "revoked_by": record.revoked_by}
                )
                self._put(self._records, org_id, stored, outbox_rows)
            for key in self._rows(self._keys, org_id):
                if key.session_id == stored.id and not key.is_destroyed():
                    self._put(self._keys, org_id, _destroyed(key, at))
            return stored

    async def rewrap_key(self, org_id: UUID, key: SessionKey, expected: bytes) -> bool:
        async with self._lock:
            stored = self._get(self._keys, org_id, key.id)
            if stored is None or stored.wrapped != expected:
                return False
            self._put(self._keys, org_id, key)
            return True

    async def read_keys_wrapped_before(
        self, org_id: UUID, before: datetime, limit: int
    ) -> list[SessionKey]:
        stale = [
            key
            for key in self._rows(self._keys, org_id)
            if key.wrapped_at is not None and key.wrapped_at < before
        ]
        return sorted(stale, key=lambda key: (key.wrapped_at or before, key.id))[:limit]

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        async with self._lock:
            gone = 0
            for table in (self._keys, self._records):
                for row in self._rows(table, org_id)[: limit - gone]:
                    del table[row.id]
                    gone += 1
            return gone


def _destroyed(key: SessionKey, at: datetime) -> SessionKey:
    return key.model_copy(
        update={"wrapped": None, "wrapping": None, "wrapped_at": None, "destroyed_at": at}
    )
