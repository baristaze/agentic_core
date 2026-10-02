from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, select

from acme.om.storage.impl.pg_base import PgStorageBase, deleted
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

    async def read_artifacts(
        self, org_id: UUID, session_id: UUID | None, limit: int
    ) -> list[Artifact]:
        stmt = select(Artifacts).where(Artifacts.org_id == org_id)
        if session_id is not None:
            stmt = stmt.where(Artifacts.session_id == session_id)
        stmt = stmt.order_by(Artifacts.id).limit(limit)
        async with self._session_for(stmt, org_id=org_id) as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [to_model(row, Artifact) for row in rows]

    async def purge_artifacts(self, org_id: UUID, artifact_ids: Sequence[UUID]) -> int:
        if not artifact_ids:
            return 0
        stmt = delete(Artifacts).where(
            Artifacts.org_id == org_id, Artifacts.id.in_(list(artifact_ids))
        )
        async with self._purge_session_for(stmt, org_id=org_id) as session:
            purged = deleted(await session.execute(stmt))
            await session.commit()
            return purged
