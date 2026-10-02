from uuid import UUID

from sqlalchemy import Update, select, update

from acme.om.agent_sessions.storage import AgentSessionStorageInterface
from acme.om.agent_sessions.storage.tables.agent_sessions import AgentSessions
from acme.om.agent_sessions.types.agent_session import AgentSession, SessionStatus
from acme.om.exceptions import PreconditionFailed
from acme.om.outbox.storage.tables.outbox_rows import OutboxRows
from acme.om.outbox.types.row import OutboxRow
from acme.om.storage.impl.pg_base import PgStorageBase, delete_batch, deleted
from acme.om.storage.utils.translation import to_model, to_row, to_values


def cas_statement(org_id: UUID, session: AgentSession, expected_version: int) -> Update:
    """The compare-and-set of one session: the version is in the WHERE, so
    two writers from one snapshot cannot both land. Returns the id when it
    hit."""
    values = {k: v for k, v in to_values(session, AgentSessions).items() if k != "id"}
    return (
        update(AgentSessions)
        .where(
            AgentSessions.id == session.id,
            AgentSessions.org_id == org_id,
            AgentSessions.version == expected_version,
        )
        .values(**values)
        .returning(AgentSessions.id)
    )


class AgentSessionStoragePostgresImpl(PgStorageBase, AgentSessionStorageInterface):
    async def create_session(
        self, org_id: UUID, session: AgentSession, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        return await self._insert(AgentSessions, org_id, session, outbox_rows)

    async def read_session(self, org_id: UUID, session_id: UUID) -> AgentSession | None:
        stmt = select(AgentSessions).where(
            AgentSessions.org_id == org_id, AgentSessions.id == session_id
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, AgentSession)

    async def read_children(
        self, org_id: UUID, parent_id: UUID, after: UUID | None, limit: int
    ) -> list[AgentSession]:
        stmt = select(AgentSessions).where(
            AgentSessions.org_id == org_id, AgentSessions.parent_id == parent_id
        )
        if after is not None:
            stmt = stmt.where(AgentSessions.id > after)
        stmt = stmt.order_by(AgentSessions.id).limit(limit)
        async with self._session_for(stmt, org_id=org_id) as session:
            return [to_model(row, AgentSession) for row in (await session.execute(stmt)).scalars()]

    async def read_sessions(
        self, org_id: UUID, status: SessionStatus | None, after: UUID | None, limit: int
    ) -> list[AgentSession]:
        stmt = select(AgentSessions).where(AgentSessions.org_id == org_id)
        if status is not None:
            stmt = stmt.where(AgentSessions.status == status.value)
        if after is not None:
            stmt = stmt.where(AgentSessions.id > after)
        stmt = stmt.order_by(AgentSessions.id).limit(limit)
        async with self._session_for(stmt, org_id=org_id) as session:
            return [to_model(row, AgentSession) for row in (await session.execute(stmt)).scalars()]

    async def write_session(
        self,
        org_id: UUID,
        session: AgentSession,
        expected_version: int,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> None:
        async with self._session_for(AgentSessions, org_id=org_id) as db:
            stmt = cas_statement(org_id, session, expected_version)
            if (await db.execute(stmt)).scalar_one_or_none() is None:
                # Moved by another writer, or gone, or another tenant's: in
                # each the caller's snapshot is stale.
                await db.rollback()
                raise PreconditionFailed(
                    f"agent session {session.id} is no longer at version {expected_version}"
                )
            for outbox_row in outbox_rows:
                db.add(to_row(outbox_row, OutboxRows, org_id=org_id))
            await db.commit()

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        stmt = delete_batch(AgentSessions, AgentSessions.org_id == org_id, limit=limit)
        async with self._session_for(stmt, org_id=org_id) as session:
            purged = deleted(await session.execute(stmt))
            await session.commit()
            return purged
