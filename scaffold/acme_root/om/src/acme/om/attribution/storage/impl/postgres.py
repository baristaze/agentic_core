from uuid import UUID

from sqlalchemy import Update, delete, select, update

from acme.om.attribution.storage import AttributionStorageInterface
from acme.om.attribution.storage.tables.session_authorities import SessionAuthorities
from acme.om.attribution.types.authority import SessionAuthority
from acme.om.exceptions import PreconditionFailed
from acme.om.outbox.storage.tables.outbox_rows import OutboxRows
from acme.om.outbox.types.row import OutboxRow
from acme.om.storage.impl.pg_base import PgStorageBase, delete_batch, deleted
from acme.om.storage.utils.translation import to_model, to_row, to_values


def cas_statement(org_id: UUID, authority: SessionAuthority, expected_version: int) -> Update:
    """The compare-and-set of one authority: the version is in the WHERE,
    so two writers from one snapshot cannot both land. Returns the id when
    it hit."""
    values = {k: v for k, v in to_values(authority, SessionAuthorities).items() if k != "id"}
    return (
        update(SessionAuthorities)
        .where(
            SessionAuthorities.id == authority.id,
            SessionAuthorities.org_id == org_id,
            SessionAuthorities.version == expected_version,
        )
        .values(**values)
        .returning(SessionAuthorities.id)
    )


class AttributionStoragePostgresImpl(PgStorageBase, AttributionStorageInterface):
    async def create_authority(
        self, org_id: UUID, authority: SessionAuthority, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        return await self._insert(SessionAuthorities, org_id, authority, outbox_rows)

    async def read_authority(self, org_id: UUID, session_id: UUID) -> SessionAuthority | None:
        stmt = select(SessionAuthorities).where(
            SessionAuthorities.org_id == org_id, SessionAuthorities.id == session_id
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, SessionAuthority)

    async def write_authority(
        self,
        org_id: UUID,
        authority: SessionAuthority,
        expected_version: int,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> None:
        async with self._session_for(SessionAuthorities, org_id=org_id) as db:
            stmt = cas_statement(org_id, authority, expected_version)
            if (await db.execute(stmt)).scalar_one_or_none() is None:
                # Moved by another writer, or gone, or another tenant's: in
                # each the caller's snapshot is stale.
                await db.rollback()
                raise PreconditionFailed(
                    f"the authority of {authority.id} is no longer at version {expected_version}"
                )
            for outbox_row in outbox_rows:
                db.add(to_row(outbox_row, OutboxRows, org_id=org_id))
            await db.commit()

    async def purge_authority(self, org_id: UUID, session_id: UUID) -> bool:
        stmt = delete(SessionAuthorities).where(
            SessionAuthorities.org_id == org_id, SessionAuthorities.id == session_id
        )
        async with self._purge_session_for(stmt, org_id=org_id) as session:
            purged = deleted(await session.execute(stmt))
            await session.commit()
            return purged > 0

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        stmt = delete_batch(SessionAuthorities, SessionAuthorities.org_id == org_id, limit=limit)
        async with self._session_for(stmt, org_id=org_id) as session:
            purged = deleted(await session.execute(stmt))
            await session.commit()
            return purged
