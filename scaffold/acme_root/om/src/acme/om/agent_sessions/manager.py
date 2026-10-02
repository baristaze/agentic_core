"""The agent sessions swimlane: sessions, each a series of loops over one
history, and the status each caches from its steps.

The steps are the truth. A step lands in the history first; the session's
cached status follows it in a write of its own, here, with the outbox row
that announces a change. That write may be late or lost, so it reads the
steps from where it last stopped and is always rebuildable from them."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.agent_sessions.types.agent_session import (
    AgentSession,
    AgentSessionPage,
    SessionStatus,
)
from acme.om.context import TenantContext


class AgentSessionsManagerInterface(ABC):
    @abstractmethod
    async def create_session(self, ctx: TenantContext, session: AgentSession) -> AgentSession:
        """The create: the session lands idle, with no history yet, and is
        announced. A session with a parent joins its parent's tree, and a
        parent the tenant does not hold is `ValidationFailed`. The root, the
        status, and the provenance are the manager's. An id written already
        answers the session as stored."""
        ...

    @abstractmethod
    async def get_session(self, ctx: TenantContext, session_id: UUID) -> AgentSession:
        """A session of the tenant; one another tenant holds is `NotFound`,
        as one that never existed is."""
        ...

    @abstractmethod
    async def get_sessions(
        self,
        ctx: TenantContext,
        status: SessionStatus | None,
        after: UUID | None,
        limit: int,
    ) -> AgentSessionPage:
        """One page of the tenant's sessions in a status, or in any, by id,
        strictly after `after`; `limit` is clamped."""
        ...

    @abstractmethod
    async def project_status(self, ctx: TenantContext, session_id: UUID) -> AgentSession:
        """Brings the cached status up to the history: reads the steps after
        the last one it read, folds them (`agent_sessions.rules.projected`),
        and writes the session conditioned on the version it read, with the
        row that announces a change of status or the end of a loop. A writer
        that got there first is read again and the fold goes on from it.
        With nothing new, the session is answered as it is."""
        ...

    @abstractmethod
    async def archive_session(self, ctx: TenantContext, session_id: UUID) -> AgentSession:
        """Sets the archive flag on an idle session; an archived one is
        answered as it is, and one with a loop open is `ValidationFailed`.
        A principal's message undoes it."""
        ...

    @abstractmethod
    async def purge_tenant(self, ctx: TenantContext) -> int:
        """The sweep, for one tenant past its own retention: every session,
        a batch at most a call. Any other tenant returns 0 and reads
        nothing."""
        ...
