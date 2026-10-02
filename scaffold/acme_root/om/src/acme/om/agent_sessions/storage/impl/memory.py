from datetime import datetime
from uuid import UUID

from acme.om.agent_sessions.storage import AgentSessionStorageInterface
from acme.om.agent_sessions.types.agent_session import AgentSession, SessionStatus
from acme.om.exceptions import PreconditionFailed
from acme.om.outbox.storage import OutboxLandingInterface
from acme.om.outbox.types.row import OutboxRow
from acme.om.storage.impl.memory_base import MemoryStorageBase, MemoryTable


class AgentSessionStorageMemoryImpl(MemoryStorageBase, AgentSessionStorageInterface):
    def __init__(self, outbox: OutboxLandingInterface | None = None) -> None:
        super().__init__(outbox)
        self._sessions: MemoryTable[AgentSession] = {}

    async def create_session(
        self, org_id: UUID, session: AgentSession, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        async with self._lock:
            return self._insert(self._sessions, org_id, session, outbox_rows)

    async def read_session(self, org_id: UUID, session_id: UUID) -> AgentSession | None:
        return self._get(self._sessions, org_id, session_id)

    async def read_children(
        self, org_id: UUID, parent_id: UUID, after: UUID | None, limit: int
    ) -> list[AgentSession]:
        return [
            s
            for s in self._rows(self._sessions, org_id)
            if s.parent_id == parent_id and (after is None or s.id > after)
        ][:limit]

    async def read_sessions(
        self, org_id: UUID, status: SessionStatus | None, after: UUID | None, limit: int
    ) -> list[AgentSession]:
        return [
            s
            for s in self._rows(self._sessions, org_id)
            if s.deleted_at is None
            and (status is None or s.status is status)
            and (after is None or s.id > after)
        ][:limit]

    async def write_session(
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
            self._sessions[session.id] = (org_id, session)

    async def read_purgeable(
        self, deleted_before: datetime, limit: int
    ) -> list[tuple[UUID, AgentSession]]:
        return [
            (org_id, s)
            for org_id, s in self._rows_across_tenants(self._sessions)
            if s.deleted_at is not None and s.deleted_at < deleted_before
        ][:limit]

    async def tree_holds_others(self, org_id: UUID, root_id: UUID, session_id: UUID) -> bool:
        return any(
            s.root_id == root_id and s.id != session_id for s in self._rows(self._sessions, org_id)
        )

    async def purge_session(self, org_id: UUID, session_id: UUID) -> bool:
        async with self._lock:
            found = self._get(self._sessions, org_id, session_id)
            if found is None or found.purge_started_at is None:
                return False
            del self._sessions[session_id]
            return True

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        async with self._lock:
            gone = [s.id for s in self._rows(self._sessions, org_id)][:limit]
            for session_id in gone:
                del self._sessions[session_id]
            return len(gone)
