from collections.abc import Collection
from datetime import datetime
from uuid import UUID

from acme.om.exceptions import KeyRevoked, NotFound, TenantMismatch, UniqueKeyTaken
from acme.om.outbox.storage import OutboxLandingInterface
from acme.om.outbox.types.row import OutboxRow
from acme.om.privacy.storage import PrivacyStorageInterface
from acme.om.privacy.types.agent_session_privacy import (
    AgentSessionKey,
    AgentSessionPrivacy,
    KeyRing,
    TenantTitleKey,
    TitleKeys,
)
from acme.om.storage.impl.memory_base import E, MemoryStorageBase, MemoryTable


class PrivacyStorageMemoryImpl(MemoryStorageBase, PrivacyStorageInterface):
    def __init__(self, outbox: OutboxLandingInterface | None = None) -> None:
        super().__init__(outbox)
        self._records: MemoryTable[AgentSessionPrivacy] = {}
        self._keys: MemoryTable[AgentSessionKey] = {}
        self._title_keys: MemoryTable[TenantTitleKey] = {}

    async def create_privacy(
        self, org_id: UUID, record: AgentSessionPrivacy, outbox_rows: tuple[OutboxRow, ...] = ()
    ) -> AgentSessionPrivacy:
        async with self._lock:
            return self._created(org_id, record, outbox_rows)

    def _record(self, org_id: UUID, agent_session_id: UUID) -> AgentSessionPrivacy | None:
        return next(
            (
                r
                for r in self._rows(self._records, org_id)
                if r.agent_session_id == agent_session_id
            ),
            None,
        )

    def _created(
        self, org_id: UUID, record: AgentSessionPrivacy, outbox_rows: tuple[OutboxRow, ...]
    ) -> AgentSessionPrivacy:
        """The twin of the insert that meets the session's unique key, and of
        the one that meets another tenant's primary key."""
        stored = self._record(org_id, record.agent_session_id)
        if stored is not None:
            return stored
        if not self._insert(self._records, org_id, record, outbox_rows):
            raise TenantMismatch(f"session privacy {record.id} is not in {org_id}")
        return record

    async def choose_policy(
        self, org_id: UUID, record: AgentSessionPrivacy, outbox_rows: tuple[OutboxRow, ...] = ()
    ) -> tuple[AgentSessionPrivacy, bool]:
        async with self._lock:
            stored = self._created(org_id, record, outbox_rows)
            if stored is record:
                return stored, True
            if stored.revoked_at is not None or stored.chosen:
                return stored, False
            stored = stored.model_copy(update={"policy": record.policy, "chosen": True})
            self._put(self._records, org_id, stored, outbox_rows)
            return stored, True

    async def read_privacy(
        self, org_id: UUID, agent_session_id: UUID
    ) -> AgentSessionPrivacy | None:
        return self._record(org_id, agent_session_id)

    async def add_title_key(self, org_id: UUID, record: AgentSessionPrivacy) -> AgentSessionPrivacy:
        if record.title_key is None:
            raise ValueError("a title key is given to be added")
        async with self._lock:
            stored = self._created(org_id, record, ())
            if stored is record or stored.revoked_at is not None or stored.title_key is not None:
                return stored
            stored = stored.model_copy(update={"title_key": record.title_key})
            self._put(self._records, org_id, stored)
            return stored

    async def read_title_keys(self, org_id: UUID, agent_session_ids: Collection[UUID]) -> TitleKeys:
        tenant = next(iter(self._rows(self._title_keys, org_id)), None)
        if tenant is None:
            return TitleKeys()
        named = set(agent_session_ids)
        return TitleKeys(
            tenant=tenant,
            sessions={
                r.agent_session_id: r.title_key
                for r in self._rows(self._records, org_id)
                if r.agent_session_id in named and r.title_key is not None and r.revoked_at is None
            },
        )

    async def add_tenant_title_key(self, org_id: UUID, key: TenantTitleKey) -> TenantTitleKey:
        async with self._lock:
            stored = next(iter(self._rows(self._title_keys, org_id)), None)
            if stored is not None:
                return stored
            if not self._insert(self._title_keys, org_id, key, ()):
                raise TenantMismatch(f"tenant title key {key.id} is not in {org_id}")
            return key

    async def rewrap_tenant_title_key(
        self, org_id: UUID, key: TenantTitleKey, expected: bytes
    ) -> bool:
        async with self._lock:
            stored = self._get(self._title_keys, org_id, key.id)
            if stored is None or stored.wrapped != expected:
                return False
            self._put(self._title_keys, org_id, key)
            return True

    async def add_key(self, org_id: UUID, key: AgentSessionKey) -> AgentSessionKey:
        async with self._lock:
            record = self._record(org_id, key.agent_session_id)
            if record is None:
                raise NotFound(f"no privacy record for session {key.agent_session_id}")
            if record.revoked_at is not None:
                raise KeyRevoked(f"the key of session {key.agent_session_id} is revoked")
            stored = self._version(org_id, key.agent_session_id, key.version)
            if stored is not None:
                return stored
            if key.id in self._keys:
                if self._keys[key.id][0] != org_id:
                    raise TenantMismatch(f"session key {key.id} is not in {org_id}")
                raise UniqueKeyTaken(f"session key {key.id} is another version's")
            self._put(self._keys, org_id, key)
            return key

    def _version(
        self, org_id: UUID, agent_session_id: UUID, version: int
    ) -> AgentSessionKey | None:
        return next(
            (
                key
                for key in self._rows(self._keys, org_id)
                if key.agent_session_id == agent_session_id and key.version == version
            ),
            None,
        )

    async def read_keys(self, org_id: UUID, agent_session_id: UUID) -> KeyRing:
        record = self._record(org_id, agent_session_id)
        keys = sorted(
            (
                key
                for key in self._rows(self._keys, org_id)
                if key.agent_session_id == agent_session_id
            ),
            key=lambda key: key.version,
        )
        return KeyRing(
            revoked=record is not None and record.revoked_at is not None, keys=tuple(keys)
        )

    async def revoke(
        self, org_id: UUID, record: AgentSessionPrivacy, outbox_rows: tuple[OutboxRow, ...] = ()
    ) -> AgentSessionPrivacy:
        at = record.revoked_at
        if at is None:
            raise ValueError("a revocation names when it happened")
        async with self._lock:
            stored = self._created(org_id, record, outbox_rows)
            if stored is not record:
                if stored.revoked_at is not None:
                    if stored.title_key is not None:
                        stored = stored.model_copy(update={"title_key": None})
                        self._put(self._records, org_id, stored)
                    return stored
                stored = stored.model_copy(
                    update={"revoked_at": at, "revoked_by": record.revoked_by, "title_key": None}
                )
                self._put(self._records, org_id, stored, outbox_rows)
            for key in self._rows(self._keys, org_id):
                if key.agent_session_id == stored.agent_session_id and not key.is_destroyed():
                    self._put(self._keys, org_id, _destroyed(key, at))
            return stored

    async def rewrap_key(self, org_id: UUID, key: AgentSessionKey, expected: bytes) -> bool:
        async with self._lock:
            stored = self._get(self._keys, org_id, key.id)
            if stored is None or stored.wrapped != expected:
                return False
            self._put(self._keys, org_id, key)
            return True

    async def read_keys_wrapped_before(
        self, org_id: UUID, before: datetime, limit: int
    ) -> list[AgentSessionKey]:
        stale = [
            (key.wrapped_at, key)
            for key in self._rows(self._keys, org_id)
            if key.wrapped_at is not None and key.wrapped_at < before
        ]
        return [key for _, key in sorted(stale, key=lambda pair: (pair[0], pair[1].id))][:limit]

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        """The versions first, so a record never goes while a version of its
        key is left, and the tenant's title key last, so it never goes while
        a title key it wraps is left."""
        async with self._lock:
            gone = _dropped(self._keys, org_id, limit)
            gone += _dropped(self._records, org_id, limit - gone)
            return gone + _dropped(self._title_keys, org_id, limit - gone)


def _dropped(table: MemoryTable[E], org_id: UUID, limit: int) -> int:
    """At most `limit` of the tenant's rows of `table`, deleted; how many."""
    rows = [row.id for row in MemoryStorageBase._rows(table, org_id)][: max(0, limit)]
    for row_id in rows:
        del table[row_id]
    return len(rows)


def _destroyed(key: AgentSessionKey, at: datetime) -> AgentSessionKey:
    return key.model_copy(
        update={"wrapped": None, "wrapping": None, "wrapped_at": None, "destroyed_at": at}
    )
