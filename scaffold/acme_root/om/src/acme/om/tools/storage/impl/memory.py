from uuid import UUID

from acme.om.exceptions import PreconditionFailed, UniqueKeyTaken
from acme.om.outbox.storage import OutboxLandingInterface
from acme.om.outbox.types.row import OutboxRow
from acme.om.storage.impl.memory_base import MemoryStorageBase, MemoryTable
from acme.om.tools.storage import ToolStorageInterface
from acme.om.tools.types.policy import ToolPolicy


class ToolStorageMemoryImpl(MemoryStorageBase, ToolStorageInterface):
    def __init__(self, outbox: OutboxLandingInterface | None = None) -> None:
        super().__init__(outbox)
        self._policies: MemoryTable[ToolPolicy] = {}

    async def read_policy(self, org_id: UUID) -> ToolPolicy | None:
        found = self._rows(self._policies, org_id)
        return found[0] if found else None

    async def create_policy(
        self, org_id: UUID, policy: ToolPolicy, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        async with self._lock:
            if policy.id in self._policies:
                return False
            # One policy a tenant: the unique index the table holds.
            if self._rows(self._policies, org_id):
                raise UniqueKeyTaken(f"tool_policies {policy.id}: the tenant holds a policy")
            return self._insert(self._policies, org_id, policy, outbox_rows)

    async def write_policy(
        self,
        org_id: UUID,
        policy: ToolPolicy,
        expected_version: int,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> None:
        async with self._lock:
            found = self._get(self._policies, org_id, policy.id)
            if found is None or found.version != expected_version:
                raise PreconditionFailed(
                    f"tool policy {policy.id} is no longer at version {expected_version}"
                )
            self._put(self._policies, org_id, policy, outbox_rows)

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        async with self._lock:
            gone = [policy.id for policy in self._rows(self._policies, org_id)][:limit]
            for policy_id in gone:
                del self._policies[policy_id]
            return len(gone)
