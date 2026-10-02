from uuid import UUID

from sqlalchemy import select

from acme.om.storage.impl.pg_base import PgStorageBase
from acme.om.storage.utils.translation import to_model
from acme.om.windows.storage import WindowStorageInterface
from acme.om.windows.storage.tables.artifacts import Artifacts
from acme.om.windows.types.artifact import Artifact


class WindowStoragePostgresImpl(PgStorageBase, WindowStorageInterface):
    async def write_artifact(self, org_id: UUID, artifact: Artifact) -> bool:
        return await self._insert(Artifacts, org_id, artifact)

    async def read_artifact(
        self, org_id: UUID, session_id: UUID, artifact_id: UUID
    ) -> Artifact | None:
        stmt = select(Artifacts).where(
            Artifacts.org_id == org_id,
            Artifacts.session_id == session_id,
            Artifacts.id == artifact_id,
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, Artifact)
