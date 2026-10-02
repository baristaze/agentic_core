from uuid import UUID

from acme.om.exceptions import PreconditionFailed
from acme.om.models.storage import FillSetStorageInterface
from acme.om.models.types.fill import FillSet
from acme.om.storage.impl.memory_base import MemoryStorageBase, MemoryTable


class FillSetStorageMemoryImpl(MemoryStorageBase, FillSetStorageInterface):
    def __init__(self) -> None:
        super().__init__()
        self._fill_sets: MemoryTable[FillSet] = {}

    def _versions(self, org_id: UUID, session_id: UUID) -> list[FillSet]:
        return sorted(
            (f for f in self._rows(self._fill_sets, org_id) if f.session_id == session_id),
            key=lambda f: f.version,
        )

    async def write_fill_set(self, org_id: UUID, fill_set: FillSet) -> bool:
        async with self._lock:
            if fill_set.id in self._fill_sets:
                return False
            # The unique version is per tenant, as the index makes it in
            # Postgres: another tenant's session holds nothing here.
            taken = any(
                f.version == fill_set.version for f in self._versions(org_id, fill_set.session_id)
            )
            if taken:
                raise PreconditionFailed(
                    f"session {fill_set.session_id} holds version {fill_set.version} already"
                )
            return self._insert(self._fill_sets, org_id, fill_set)

    async def read_fill_set(
        self, org_id: UUID, session_id: UUID, version: int | None
    ) -> FillSet | None:
        versions = self._versions(org_id, session_id)
        if version is None:
            return versions[-1] if versions else None
        return next((f for f in versions if f.version == version), None)

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        async with self._lock:
            gone = [f.id for f in self._rows(self._fill_sets, org_id)][:limit]
            for fill_set_id in gone:
                del self._fill_sets[fill_set_id]
            return len(gone)
