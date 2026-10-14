from uuid import UUID

from sqlalchemy import Update, delete, select, update

from acme.om.attribution.storage import AttributionStorageInterface
from acme.om.attribution.storage.tables.agent_session_authorities import AgentSessionAuthorities
from acme.om.attribution.types.authority import AgentSessionAuthority
from acme.om.exceptions import PreconditionFailed
from acme.om.outbox.storage.tables.outbox_rows import OutboxRows
from acme.om.outbox.types.row import OutboxRow
from acme.om.storage.impl.pg_base import PgStorageBase, delete_batch, deleted
from acme.om.storage.utils.translation import to_model, to_row, to_values


def cas_statement(org_id: UUID, authority: AgentSessionAuthority, expected_version: int) -> Update:
    """The compare-and-set of one authority: the version is in the WHERE,
    so two writers from one snapshot cannot both land. Returns the id when
    it hit."""
    values = {k: v for k, v in to_values(authority, AgentSessionAuthorities).items() if k != "id"}
    return (
        update(AgentSessionAuthorities)
        .where(
            AgentSessionAuthorities.id == authority.id,
            AgentSessionAuthorities.org_id == org_id,
            AgentSessionAuthorities.version == expected_version,
        )
        .values(**values)
        .returning(AgentSessionAuthorities.id)
    )


class AttributionStoragePostgresImpl(PgStorageBase, AttributionStorageInterface):
    async def create_authority(
        self, org_id: UUID, authority: AgentSessionAuthority, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        return await self._insert(AgentSessionAuthorities, org_id, authority, outbox_rows)

    async def read_authority(
        self, org_id: UUID, agent_session_id: UUID
    ) -> AgentSessionAuthority | None:
        stmt = select(AgentSessionAuthorities).where(
            AgentSessionAuthorities.org_id == org_id, AgentSessionAuthorities.id == agent_session_id
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, AgentSessionAuthority)

    async def write_authority(
        self,
        org_id: UUID,
        authority: AgentSessionAuthority,
        expected_version: int,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> None:
        async with self._session_for(AgentSessionAuthorities, org_id=org_id) as db:
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

    async def purge_authority(self, org_id: UUID, agent_session_id: UUID) -> bool:
        stmt = delete(AgentSessionAuthorities).where(
            AgentSessionAuthorities.org_id == org_id, AgentSessionAuthorities.id == agent_session_id
        )
        async with self._purge_session_for(stmt, org_id=org_id) as session:
            purged = deleted(await session.execute(stmt))
            await session.commit()
            return purged > 0

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        stmt = delete_batch(
            AgentSessionAuthorities, AgentSessionAuthorities.org_id == org_id, limit=limit
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            purged = deleted(await session.execute(stmt))
            await session.commit()
            return purged
