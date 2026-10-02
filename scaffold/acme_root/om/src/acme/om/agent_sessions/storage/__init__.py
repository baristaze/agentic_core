"""Storage of the agent sessions swimlane. Every operation takes org_id
first. A session's every write after the create is a compare-and-set on its
version, landed with its outbox rows in one commit."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.agent_sessions.types.agent_session import AgentSession, SessionStatus
from acme.om.outbox.types.row import OutboxRow


class AgentSessionStorageInterface(ABC):
    @abstractmethod
    async def create_session(
        self, org_id: UUID, session: AgentSession, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        """The create, with the rows that announce it, in one commit; False,
        with nothing landed, when the id is written already."""
        ...

    @abstractmethod
    async def read_session(self, org_id: UUID, session_id: UUID) -> AgentSession | None: ...

    @abstractmethod
    async def read_sessions(
        self, org_id: UUID, status: SessionStatus | None, after: UUID | None, limit: int
    ) -> list[AgentSession]:
        """The tenant's sessions in a status, or in any, by id, strictly
        after `after`, at most `limit` of them."""
        ...

    @abstractmethod
    async def write_session(
        self,
        org_id: UUID,
        session: AgentSession,
        expected_version: int,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> None:
        """The compare-and-set: lands the session and its outbox rows
        together when the stored one is at `expected_version`, and raises
        `PreconditionFailed` otherwise, landing nothing."""
        ...

    @abstractmethod
    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        """At most `limit` sessions of a deleted tenant past its retention;
        returns how many went."""
        ...
