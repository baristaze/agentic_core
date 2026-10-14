from uuid import UUID

from acme.om.attribution.storage import AttributionStorageInterface
from acme.om.attribution.types.authority import AgentSessionAuthority
from acme.om.exceptions import PreconditionFailed
from acme.om.outbox.storage import OutboxLandingInterface
from acme.om.outbox.types.row import OutboxRow
from acme.om.storage.impl.memory_base import MemoryStorageBase, MemoryTable


class AttributionStorageMemoryImpl(MemoryStorageBase, AttributionStorageInterface):
    def __init__(self, outbox: OutboxLandingInterface | None = None) -> None:
        super().__init__(outbox)
        self._authorities: MemoryTable[AgentSessionAuthority] = {}

    async def create_authority(
        self, org_id: UUID, authority: AgentSessionAuthority, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        async with self._lock:
            return self._insert(self._authorities, org_id, authority, outbox_rows)

    async def read_authority(
        self, org_id: UUID, agent_session_id: UUID
    ) -> AgentSessionAuthority | None:
        return self._get(self._authorities, org_id, agent_session_id)

    async def write_authority(
        self,
        org_id: UUID,
        authority: AgentSessionAuthority,
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

    async def purge_authority(self, org_id: UUID, agent_session_id: UUID) -> bool:
        async with self._lock:
            if self._get(self._authorities, org_id, agent_session_id) is None:
                return False
            del self._authorities[agent_session_id]
            return True

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        async with self._lock:
            gone = [a.id for a in self._rows(self._authorities, org_id)][:limit]
            for agent_session_id in gone:
                del self._authorities[agent_session_id]
            return len(gone)
