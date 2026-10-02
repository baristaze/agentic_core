from uuid import UUID

from acme.om.attribution.storage import AttributionStorageInterface
from acme.om.attribution.types.authority import SessionAuthority
from acme.om.exceptions import PreconditionFailed
from acme.om.outbox.storage import OutboxLandingInterface
from acme.om.outbox.types.row import OutboxRow
from acme.om.storage.impl.memory_base import MemoryStorageBase, MemoryTable


class AttributionStorageMemoryImpl(MemoryStorageBase, AttributionStorageInterface):
    def __init__(self, outbox: OutboxLandingInterface | None = None) -> None:
        super().__init__(outbox)
        self._authorities: MemoryTable[SessionAuthority] = {}

    async def create_authority(
        self, org_id: UUID, authority: SessionAuthority, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        async with self._lock:
            return self._insert(self._authorities, org_id, authority, outbox_rows)

    async def read_authority(self, org_id: UUID, session_id: UUID) -> SessionAuthority | None:
        return self._get(self._authorities, org_id, session_id)

    async def write_authority(
        self,
        org_id: UUID,
        authority: SessionAuthority,
        expected_version: int,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> None:
        async with self._lock:
            # Another tenant's authority is none here, as the policy makes it
            # in Postgres: the write misses, and the snapshot is stale.
            found = self._get(self._authorities, org_id, authority.id)
            if found is None or found.version != expected_version:
                raise PreconditionFailed(
                    f"the authority of {authority.id} is no longer at version {expected_version}"
                )
            self._put(self._authorities, org_id, authority, outbox_rows)

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        async with self._lock:
            gone = [a.id for a in self._rows(self._authorities, org_id)][:limit]
            for session_id in gone:
                del self._authorities[session_id]
            return len(gone)
