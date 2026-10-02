"""Storage of the privacy swimlane: each session's privacy record and the
versions of its key, wrapped. Every operation takes org_id first.

The record is written once and changes only by the revocation. A version is
added under the record's lock, so a version never lands after the key is
revoked, and the revocation destroys every version in the same commit that
marks the record."""

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from acme.om.outbox.types.row import OutboxRow
from acme.om.privacy.types.session_privacy import KeyRing, SessionKey, SessionPrivacy


class PrivacyStorageInterface(ABC):
    @abstractmethod
    async def create_privacy(
        self, org_id: UUID, record: SessionPrivacy, outbox_rows: tuple[OutboxRow, ...] = ()
    ) -> SessionPrivacy:
        """The session's record, written once: `record` lands with the rows
        that announce it when the session has none, and the stored one is
        answered either way, with nothing landed."""
        ...

    @abstractmethod
    async def read_privacy(self, org_id: UUID, session_id: UUID) -> SessionPrivacy | None: ...

    @abstractmethod
    async def add_key(self, org_id: UUID, key: SessionKey) -> SessionKey:
        """Version `key.version` of its session's key, under the session's
        record's lock: it lands when the session holds no such version, and
        the stored one is answered either way. `KeyRevoked`, with nothing
        written, when the key is revoked; `NotFound` when the session has no
        record."""
        ...

    @abstractmethod
    async def read_keys(self, org_id: UUID, session_id: UUID) -> KeyRing:
        """Every version of the session's key in version order, live and
        destroyed, and whether it is revoked."""
        ...

    @abstractmethod
    async def revoke(
        self, org_id: UUID, record: SessionPrivacy, outbox_rows: tuple[OutboxRow, ...] = ()
    ) -> SessionPrivacy:
        """In one commit, with the rows that announce it: the session's
        record takes the revocation `record` carries, or lands as `record`
        when the session has none, and every version of its key is destroyed
        at that time: its wrapped copy goes, its row stays. A session revoked
        before is answered as it is, with nothing destroyed and nothing
        landed."""
        ...

    @abstractmethod
    async def rewrap_key(self, org_id: UUID, key: SessionKey, expected: bytes) -> bool:
        """The compare-and-set of a rotation: `key`'s wrapped copy replaces
        the stored one when the stored one is still `expected`. False, with
        nothing written, when it is not, such as a version destroyed
        meanwhile."""
        ...

    @abstractmethod
    async def read_keys_wrapped_before(
        self, org_id: UUID, before: datetime, limit: int
    ) -> list[SessionKey]:
        """At most `limit` live versions of the tenant's keys wrapped before
        `before`, the longest wrapped first."""
        ...

    @abstractmethod
    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        """At most `limit` rows of a deleted tenant past its retention, the
        versions before the records; returns how many."""
        ...
