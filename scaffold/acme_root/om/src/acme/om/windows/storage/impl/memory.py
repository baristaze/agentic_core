from uuid import UUID

from acme.om.storage.impl.memory_base import MemoryStorageBase, MemoryTable
from acme.om.windows.storage import WindowStorageInterface
from acme.om.windows.types.artifact import Artifact


class WindowStorageMemoryImpl(MemoryStorageBase, WindowStorageInterface):
    def __init__(self) -> None:
        super().__init__()
        self._artifacts: MemoryTable[Artifact] = {}

    async def write_artifact(self, org_id: UUID, artifact: Artifact) -> bool:
        async with self._lock:
            return self._insert(self._artifacts, org_id, artifact)

    async def read_artifact(
        self, org_id: UUID, session_id: UUID, artifact_id: UUID
    ) -> Artifact | None:
        found = self._get(self._artifacts, org_id, artifact_id)
        return found if found is not None and found.session_id == session_id else None
