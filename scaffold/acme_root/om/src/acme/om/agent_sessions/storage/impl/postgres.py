from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Update, delete, exists, or_, select, update

from acme.om.agent_sessions.storage import AgentSessionStorageInterface
from acme.om.agent_sessions.storage.tables.agent_sessions import SEALED_TITLE, AgentSessions
from acme.om.agent_sessions.types.agent_session import (
    AgentSession,
    AgentSessionStatus,
    Among,
    Title,
)
from acme.om.agent_steps.types.content import ContentState
from acme.om.attribution.types.authority import Visibility
from acme.om.base import EMPTY_UUID
from acme.om.exceptions import PreconditionFailed
from acme.om.outbox.storage.tables.outbox_rows import OutboxRows
from acme.om.outbox.types.row import OutboxRow
from acme.om.storage.impl.pg_base import PLAN_WITH_VALUES, PgStorageBase, deleted
from acme.om.storage.utils.translation import to_row, to_values

TITLE_COLUMNS = ("title", "title_sealed")
"""Written by the create and by the sweep's seal alone."""


def title_values(title: Title) -> dict[str, Any]:
    """The title columns of a title sealed or absent; a plain one is refused,
    since only the manager seals it."""
    if title.state is ContentState.PLAIN:
        raise ValueError("a session's title is kept sealed, never in the clear")
    return {"title": SEALED_TITLE, "title_sealed": title.sealed or b""}


def stored_title(row: AgentSessions) -> Title:
    """The title a row holds: sealed, absent once emptied, or in the clear
    while it is from before titles were sealed."""
    if row.title_sealed is None:
        return Title(text=row.title)
    if not row.title_sealed:
        return Title(state=ContentState.ABSENT)
    return Title(state=ContentState.SEALED, sealed=row.title_sealed)


def to_session(row: AgentSessions) -> AgentSession:
    fields = {name: getattr(row, name) for name in AgentSession.model_fields if name != "title"}
    return AgentSession.model_validate({**fields, "title": stored_title(row)})


def cas_statement(org_id: UUID, session: AgentSession, expected_version: int) -> Update:
    """The compare-and-set of one session: the version is in the WHERE, so
    two writers from one snapshot cannot both land. Returns the id when it
    hit. The title is left as it is stored."""
    values = {
        k: v
        for k, v in to_values(session, AgentSessions).items()
        if k != "id" and k not in TITLE_COLUMNS
    }
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
    async def create_agent_session(
        self, org_id: UUID, session: AgentSession, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        return await self._insert(
            AgentSessions, org_id, session, outbox_rows, **title_values(session.title)
        )

    async def read_agent_session(self, org_id: UUID, agent_session_id: UUID) -> AgentSession | None:
        stmt = select(AgentSessions).where(
            AgentSessions.org_id == org_id, AgentSessions.id == agent_session_id
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_session(row)

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
            return [to_session(row) for row in (await session.execute(stmt)).scalars()]

    async def read_agent_sessions(
        self,
        org_id: UUID,
        status: AgentSessionStatus | None,
        after: UUID | None,
        limit: int,
        *,
        among: Among | None = None,
        person: UUID | None = None,
    ) -> list[AgentSession]:
        stmt = select(AgentSessions).where(
            AgentSessions.org_id == org_id, AgentSessions.deleted_at.is_(None)
        )
        if status is not None:
            stmt = stmt.where(AgentSessions.status == status.value)
        if among is not None:
            stmt = stmt.where(AgentSessions.root_id == AgentSessions.id)
        if among is not None and person is not None:
            mine = [
                AgentSessions.created_by == person,
                AgentSessions.participants.contains([str(person)]),
            ]
            if among is Among.VISIBLE:
                mine.append(AgentSessions.visibility == Visibility.ORG_WIDE.value)
            stmt = stmt.where(or_(*mine))
        if after is not None:
            stmt = stmt.where(AgentSessions.id > after)
        stmt = stmt.order_by(AgentSessions.id).limit(limit)
        async with self._session_for(stmt, org_id=org_id) as session:
            return [to_session(row) for row in (await session.execute(stmt)).scalars()]

    async def write_agent_session(
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

    async def read_purgeable(
        self, deleted_before: datetime, limit: int
    ) -> list[tuple[UUID, AgentSession]]:
        # No order: the batch is any `limit` of the rows the partial index
        # holds past the cut, so a backlog is never sorted to take a batch.
        stmt = select(AgentSessions).where(AgentSessions.deleted_at < deleted_before).limit(limit)
        # Every tenant's sessions past their cut, so the system scope, spelled
        # here, planned with its values (ADR 0056).
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            await session.execute(PLAN_WITH_VALUES)
            rows = (await session.execute(stmt)).scalars()
            return [(row.org_id, to_session(row)) for row in rows]

    async def read_clear_titles(self, limit: int) -> list[tuple[UUID, AgentSession]]:
        # Any `limit` of the rows the partial index holds; once every title
        # is sealed it holds none, so a pass costs one probe of it.
        stmt = select(AgentSessions).where(AgentSessions.title_sealed.is_(None)).limit(limit)
        # Every tenant's sessions, so the system scope, spelled here, planned
        # with its values (ADR 0056).
        async with self._session_for(stmt, org_id=EMPTY_UUID) as session:
            await session.execute(PLAN_WITH_VALUES)
            rows = (await session.execute(stmt)).scalars()
            return [(row.org_id, to_session(row)) for row in rows]

    async def seal_title(self, org_id: UUID, session: AgentSession, expected_version: int) -> bool:
        stmt = (
            update(AgentSessions)
            .where(
                AgentSessions.id == session.id,
                AgentSessions.org_id == org_id,
                AgentSessions.version == expected_version,
                AgentSessions.title_sealed.is_(None),
            )
            .values(
                **title_values(session.title),
                version=session.version,
                updated_at=session.updated_at,
                updated_by=session.updated_by,
            )
            .returning(AgentSessions.id)
        )
        async with self._session_for(stmt, org_id=org_id) as db:
            landed = (await db.execute(stmt)).scalar_one_or_none() is not None
            await db.commit()
            return landed

    async def tree_holds_others(self, org_id: UUID, root_id: UUID, agent_session_id: UUID) -> bool:
        stmt = select(
            exists().where(
                AgentSessions.org_id == org_id,
                AgentSessions.root_id == root_id,
                AgentSessions.id != agent_session_id,
            )
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            return bool((await session.execute(stmt)).scalar_one())

    async def purge_agent_session(self, org_id: UUID, agent_session_id: UUID) -> bool:
        stmt = delete(AgentSessions).where(
            AgentSessions.org_id == org_id,
            AgentSessions.id == agent_session_id,
            AgentSessions.purge_started_at.is_not(None),
        )
        async with self._purge_session_for(stmt, org_id=org_id) as session:
            purged = deleted(await session.execute(stmt))
            await session.commit()
            return purged > 0

    async def read_tenant_agent_sessions(self, org_id: UUID, limit: int) -> list[UUID]:
        stmt = select(AgentSessions.id).where(AgentSessions.org_id == org_id).limit(limit)
        async with self._session_for(stmt, org_id=org_id) as session:
            return list((await session.execute(stmt)).scalars())

    async def purge_tenant(self, org_id: UUID, agent_session_ids: Sequence[UUID]) -> int:
        if not agent_session_ids:
            return 0
        # The funnel holds the tenant's purge lock, so two workers' deletes
        # of one tenant take turns (`hold_purge`).
        stmt = delete(AgentSessions).where(
            AgentSessions.org_id == org_id, AgentSessions.id.in_(agent_session_ids)
        )
        async with self._purge_session_for(stmt, org_id=org_id) as session:
            purged = deleted(await session.execute(stmt))
            await session.commit()
            return purged
