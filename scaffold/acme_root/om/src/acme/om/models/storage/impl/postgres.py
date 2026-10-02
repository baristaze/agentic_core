from uuid import UUID

from sqlalchemy import select

from acme.om.exceptions import PreconditionFailed, UniqueKeyTaken
from acme.om.models.storage import FillSetStorageInterface
from acme.om.models.storage.tables.fill_sets import FillSets
from acme.om.models.types.fill import FillSet
from acme.om.storage.impl.pg_base import PgStorageBase, delete_batch, deleted
from acme.om.storage.utils.translation import to_model


class FillSetStoragePostgresImpl(PgStorageBase, FillSetStorageInterface):
    async def write_fill_set(self, org_id: UUID, fill_set: FillSet) -> bool:
        try:
            return await self._insert(FillSets, org_id, fill_set)
        except UniqueKeyTaken as error:
            # The unique (org_id, session_id, version): another writer made
            # this version first.
            raise PreconditionFailed(
                f"session {fill_set.session_id} holds version {fill_set.version} already"
            ) from error

    async def read_fill_set(
        self, org_id: UUID, session_id: UUID, version: int | None
    ) -> FillSet | None:
        stmt = select(FillSets).where(FillSets.org_id == org_id, FillSets.session_id == session_id)
        if version is not None:
            stmt = stmt.where(FillSets.version == version)
        stmt = stmt.order_by(FillSets.version.desc()).limit(1)
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, FillSet)

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        stmt = delete_batch(FillSets, FillSets.org_id == org_id, limit=limit)
        async with self._session_for(stmt, org_id=org_id) as session:
            purged = deleted(await session.execute(stmt))
            await session.commit()
            return purged
