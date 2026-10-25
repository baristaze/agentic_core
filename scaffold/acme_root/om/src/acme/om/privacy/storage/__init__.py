"""Storage of the privacy swimlane: each session's privacy record and the
versions of its key, wrapped. Every operation takes org_id first.

The record is written once, and changes only by the one choice of its
policy, the title key its title brings, and the revocation. A version
and a title key are added under the record's lock, so neither lands
after the key is revoked, and the revocation destroys every version and
clears the title key in the same commit that marks the record. The
tenant's title key, one per tenant, wraps its sessions' title keys."""

from abc import ABC, abstractmethod
from collections.abc import Collection
from datetime import datetime
from uuid import UUID

from acme.om.outbox.types.row import OutboxRow
from acme.om.privacy.types.agent_session_privacy import (
    AgentSessionKey,
    AgentSessionPrivacy,
    KeyRing,
    TenantTitleKey,
    TitleKeys,
)


class PrivacyStorageInterface(ABC):
    @abstractmethod
    async def create_privacy(
        self, org_id: UUID, record: AgentSessionPrivacy, outbox_rows: tuple[OutboxRow, ...] = ()
    ) -> AgentSessionPrivacy:
        """The session's record, written once: `record` lands with the rows
        that announce it when the session has none, and the stored one is
        answered either way, with nothing landed."""
        ...

    @abstractmethod
    async def choose_policy(
        self, org_id: UUID, record: AgentSessionPrivacy, outbox_rows: tuple[OutboxRow, ...] = ()
    ) -> tuple[AgentSessionPrivacy, bool]:
        """`record`'s policy, chosen once: `record` lands with the rows that
        announce it when the session has none, or the stored one takes its
        policy when none is chosen yet, as its title wrote it, and its key
        is not revoked. Answers the stored record, and whether the rows
        landed with it."""
        ...

    @abstractmethod
    async def read_privacy(
        self, org_id: UUID, agent_session_id: UUID
    ) -> AgentSessionPrivacy | None: ...

    @abstractmethod
    async def add_title_key(self, org_id: UUID, record: AgentSessionPrivacy) -> AgentSessionPrivacy:
        """The title key `record` carries, under the session's record's lock:
        `record` lands when the session has none, or the stored one takes the
        key when it holds none and its key is not revoked. Answers the stored
        record either way, so a call that lost the race reads the winner's
        key, and a revoked one holds none."""
        ...

    @abstractmethod
    async def read_title_keys(self, org_id: UUID, agent_session_ids: Collection[UUID]) -> TitleKeys:
        """One statement: the tenant's title key, and the wrapped title key
        of each of `agent_session_ids` that holds one; a session revoked, or
        with no title sealed, is left out."""
        ...

    @abstractmethod
    async def add_tenant_title_key(self, org_id: UUID, key: TenantTitleKey) -> TenantTitleKey:
        """The tenant's title key: `key` lands when the tenant has none, and
        the stored one is answered either way."""
        ...

    @abstractmethod
    async def rewrap_tenant_title_key(
        self, org_id: UUID, key: TenantTitleKey, expected: bytes
    ) -> bool:
        """The compare-and-set of a rotation: `key`'s wrapped copy replaces
        the stored one when the stored one is still `expected`."""
        ...

    @abstractmethod
    async def add_key(self, org_id: UUID, key: AgentSessionKey) -> AgentSessionKey:
        """Version `key.version` of its session's key, under the session's
        record's lock: it lands when the session holds no such version, and
        the stored one is answered either way. `KeyRevoked`, with nothing
        written, when the key is revoked; `NotFound` when the session has no
        record."""
        ...

    @abstractmethod
    async def read_keys(self, org_id: UUID, agent_session_id: UUID) -> KeyRing:
        """Every version of the session's key in version order, live and
        destroyed, and whether it is revoked."""
        ...

    @abstractmethod
    async def revoke(
        self, org_id: UUID, record: AgentSessionPrivacy, outbox_rows: tuple[OutboxRow, ...] = ()
    ) -> AgentSessionPrivacy:
        """In one commit, with the rows that announce it: the session's
        record takes the revocation `record` carries, or lands as `record`
        when the session has none, and every version of its key is destroyed
        at that time: its wrapped copy goes, its row stays. The title key
        goes with them. A session revoked before is answered as it is, with
        nothing destroyed and nothing landed."""
        ...

    @abstractmethod
    async def rewrap_key(self, org_id: UUID, key: AgentSessionKey, expected: bytes) -> bool:
        """The compare-and-set of a rotation: `key`'s wrapped copy replaces
        the stored one when the stored one is still `expected`. False, with
        nothing written, when it is not, such as a version destroyed
        meanwhile."""
        ...

    @abstractmethod
    async def read_keys_wrapped_before(
        self, org_id: UUID, before: datetime, limit: int
    ) -> list[AgentSessionKey]:
        """At most `limit` live versions of the tenant's keys wrapped before
        `before`, the longest wrapped first."""
        ...

    @abstractmethod
    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        """At most `limit` rows of a deleted tenant past its retention, the
        versions before the records and the records before the tenant's
        title key; returns how many."""
        ...
