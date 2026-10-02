from uuid import UUID

from acme.om.agents.storage import AgentTreeStorageInterface
from acme.om.agents.types.tree import AgentTree
from acme.om.exceptions import PreconditionFailed
from acme.om.outbox.storage import OutboxLandingInterface
from acme.om.outbox.types.row import OutboxRow
from acme.om.storage.impl.memory_base import MemoryStorageBase, MemoryTable


class AgentTreeStorageMemoryImpl(MemoryStorageBase, AgentTreeStorageInterface):
    def __init__(self, outbox: OutboxLandingInterface | None = None) -> None:
        super().__init__(outbox)
        self._trees: MemoryTable[AgentTree] = {}

    async def create_tree(
        self, org_id: UUID, tree: AgentTree, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        async with self._lock:
            return self._insert(self._trees, org_id, tree, outbox_rows)

    async def read_tree(self, org_id: UUID, tree_id: UUID) -> AgentTree | None:
        return self._get(self._trees, org_id, tree_id)

    async def take_slot(self, org_id: UUID, tree_id: UUID) -> AgentTree | None:
        async with self._lock:
            found = self._get(self._trees, org_id, tree_id)
            if found is None or found.size >= found.count:
                return None
            taken = found.model_copy(update={"size": found.size + 1, "version": found.version + 1})
            self._put(self._trees, org_id, taken)
            return taken

    async def write_tree(
        self,
        org_id: UUID,
        tree: AgentTree,
        expected_version: int,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> None:
        async with self._lock:
            # Another tenant's tree is no tree here, as the policy makes it
            # in Postgres: the write misses, and the snapshot is stale.
            found = self._get(self._trees, org_id, tree.id)
            if found is None or found.version != expected_version:
                raise PreconditionFailed(
                    f"agent tree {tree.id} is no longer at version {expected_version}"
                )
            self._put(self._trees, org_id, tree, outbox_rows)
