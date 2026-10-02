from collections.abc import Sequence
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

    async def read_artifacts(
        self, org_id: UUID, session_id: UUID | None, limit: int
    ) -> list[Artifact]:
        found = [
            artifact
            for artifact in self._rows(self._artifacts, org_id)
            if session_id is None or artifact.session_id == session_id
        ]
        return found[:limit]

    async def purge_artifacts(self, org_id: UUID, artifact_ids: Sequence[UUID]) -> int:
        async with self._lock:
            gone = [i for i in set(artifact_ids) if self._get(self._artifacts, org_id, i)]
            for artifact_id in gone:
                del self._artifacts[artifact_id]
            return len(gone)
