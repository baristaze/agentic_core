"""Storage of the attribution swimlane: each session's authority, one
record keyed by the session's id. Every operation takes org_id first. A
write after the create is a compare-and-set on the version, landed with
its outbox rows in one commit."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.attribution.types.authority import SessionAuthority
from acme.om.outbox.types.row import OutboxRow


class AttributionStorageInterface(ABC):
    @abstractmethod
    async def create_authority(
        self, org_id: UUID, authority: SessionAuthority, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        """The create, with the rows that announce it, in one commit; False,
        with nothing landed, when the id is written already."""
        ...

    @abstractmethod
    async def read_authority(self, org_id: UUID, session_id: UUID) -> SessionAuthority | None: ...

    @abstractmethod
    async def write_authority(
        self,
        org_id: UUID,
        authority: SessionAuthority,
        expected_version: int,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> None:
        """The compare-and-set: lands the authority and its outbox rows
        together when the stored one is at `expected_version`, and raises
        `PreconditionFailed` otherwise, landing nothing."""
        ...

    @abstractmethod
    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        """At most `limit` authorities of a deleted tenant past its
        retention; returns how many went."""
        ...
