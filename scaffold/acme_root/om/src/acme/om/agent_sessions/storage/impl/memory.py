from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from acme.om.agent_sessions.storage import AgentSessionStorageInterface
from acme.om.agent_sessions.types.agent_session import AgentSession, AgentSessionStatus, Among
from acme.om.agent_steps.types.content import ContentState
from acme.om.attribution.types.authority import Visibility
from acme.om.exceptions import PreconditionFailed
from acme.om.outbox.storage import OutboxLandingInterface
from acme.om.outbox.types.row import OutboxRow
from acme.om.storage.impl.memory_base import MemoryStorageBase, MemoryTable


class AgentSessionStorageMemoryImpl(MemoryStorageBase, AgentSessionStorageInterface):
    def __init__(self, outbox: OutboxLandingInterface | None = None) -> None:
        super().__init__(outbox)
        self._sessions: MemoryTable[AgentSession] = {}

    async def create_agent_session(
        self, org_id: UUID, session: AgentSession, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        if session.title.state is ContentState.PLAIN:
            raise ValueError("a session's title is kept sealed, never in the clear")
        async with self._lock:
            return self._insert(self._sessions, org_id, session, outbox_rows)

    async def read_agent_session(self, org_id: UUID, agent_session_id: UUID) -> AgentSession | None:
        return self._get(self._sessions, org_id, agent_session_id)

    async def read_children(
        self, org_id: UUID, parent_id: UUID, after: UUID | None, limit: int
    ) -> list[AgentSession]:
        return [
            s
            for s in self._rows(self._sessions, org_id)
            if s.parent_id == parent_id and (after is None or s.id > after)
        ][:limit]

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
        return [
            s
            for s in self._rows(self._sessions, org_id)
            if s.deleted_at is None
            and (status is None or s.status is status)
            and (after is None or s.id > after)
            and (among is None or _listed(s, among, person))
        ][:limit]

    async def write_agent_session(
        self,
        org_id: UUID,
        session: AgentSession,
        expected_version: int,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> None:
        async with self._lock:
            # Another tenant's session is no session here, as the policy
            # makes it in Postgres: the write misses, and the snapshot is stale.
            found = self._get(self._sessions, org_id, session.id)
            if found is None or found.version != expected_version:
                raise PreconditionFailed(
                    f"agent session {session.id} is no longer at version {expected_version}"
                )
            # The version is the whole guard, as the statement's WHERE is in
            # Postgres: a write that read the session before a delete is
            # refused by it, so an unmark is a write like any other.
            self._land(org_id, outbox_rows)
            self._sessions[session.id] = (org_id, session.model_copy(update={"title": found.title}))

    async def read_purgeable(
        self, deleted_before: datetime, limit: int
    ) -> list[tuple[UUID, AgentSession]]:
        return [
            (org_id, s)
            for org_id, s in self._rows_across_tenants(self._sessions)
            if s.deleted_at is not None and s.deleted_at < deleted_before
        ][:limit]

    async def read_clear_titles(self, limit: int) -> list[tuple[UUID, AgentSession]]:
        return [
            (org_id, s)
            for org_id, s in self._rows_across_tenants(self._sessions)
            if s.title.state is ContentState.PLAIN
        ][:limit]

    async def seal_title(self, org_id: UUID, session: AgentSession, expected_version: int) -> bool:
        if session.title.state is ContentState.PLAIN:
            raise ValueError("a session's title is kept sealed, never in the clear")
        async with self._lock:
            found = self._get(self._sessions, org_id, session.id)
            if (
                found is None
                or found.version != expected_version
                or found.title.state is not ContentState.PLAIN
            ):
                return False
            sealed = found.model_copy(
                update={
                    "title": session.title,
                    "version": session.version,
                    "updated_at": session.updated_at,
                    "updated_by": session.updated_by,
                }
            )
            self._sessions[session.id] = (org_id, sealed)
            return True

    async def tree_holds_others(self, org_id: UUID, root_id: UUID, agent_session_id: UUID) -> bool:
        return any(
            s.root_id == root_id and s.id != agent_session_id
            for s in self._rows(self._sessions, org_id)
        )

    async def purge_agent_session(self, org_id: UUID, agent_session_id: UUID) -> bool:
        async with self._lock:
            found = self._get(self._sessions, org_id, agent_session_id)
            if found is None or found.purge_started_at is None:
                return False
            del self._sessions[agent_session_id]
            return True

    async def read_tenant_agent_sessions(self, org_id: UUID, limit: int) -> list[UUID]:
        return [s.id for s in self._rows(self._sessions, org_id)][:limit]

    async def purge_tenant(self, org_id: UUID, agent_session_ids: Sequence[UUID]) -> int:
        async with self._lock:
            gone = {
                i for i in agent_session_ids if self._get(self._sessions, org_id, i) is not None
            }
            for agent_session_id in gone:
                del self._sessions[agent_session_id]
            return len(gone)


def _listed(session: AgentSession, among: Among, person: UUID | None) -> bool:
    """Whether a root is on a person's list, as the Postgres statement
    filters it."""
    if session.root_id != session.id:
        return False
    if person is None:
        return True
    mine = session.created_by == person or person in session.participants
    return mine or (among is Among.VISIBLE and session.visibility is Visibility.ORG_WIDE)
