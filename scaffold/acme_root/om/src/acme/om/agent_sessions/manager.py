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
        """A session of the tenant; one another tenant holds, or one marked
        deleted, is `NotFound`, as one that never existed is."""
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
        strictly after `after`; `limit` is clamped. A session marked deleted
        is on no page."""
        ...

    @abstractmethod
    async def project_status(self, ctx: TenantContext, session_id: UUID) -> AgentSession:
        """Brings the cached status up to the history: reads the steps after
        the last one it read, folds them (`agent_sessions.rules.projected`),
        and writes the session conditioned on the version it read, with the
        row that announces a change of status or the end of a loop. A writer
        that got there first is read again and the fold goes on from it.
        With nothing new, the session is answered as it is. A session marked
        deleted is `NotFound`; once unmarked, the fold goes on from where it
        stopped."""
        ...

    @abstractmethod
    async def archive_session(self, ctx: TenantContext, session_id: UUID) -> AgentSession:
        """Sets the archive flag on an idle session; an archived one is
        answered as it is, and one with a loop open is `ValidationFailed`.
        A principal's message undoes it."""
        ...

    @abstractmethod
    async def delete_session(self, ctx: TenantContext, session_id: UUID) -> AgentSession:
        """Marks an idle session deleted, and announces it. From then on every
        read of it answers as one that never existed, while its history and
        its shape stay as they were, until it is unmarked or its retention
        ends. One with a loop open is `ValidationFailed`."""
        ...

    @abstractmethod
    async def restore_session(self, ctx: TenantContext, session_id: UUID) -> AgentSession:
        """Unmarks a session marked deleted: it comes back as it was, with
        its history, and is announced. One not marked is answered as it is.
        One the sweep has claimed for its purge is `NotFound`, as one gone
        or another tenant's is: past its retention, the delete is final."""
        ...

    @abstractmethod
    async def purge_across_tenants(self) -> int:
        """Platform-internal: the sweep, across tenants, once a pass, for no
        tenant and no principal: the sessions marked deleted longer ago than
        the retention, a batch at most a call. Each is claimed first, by a compare-and-set that makes
        its delete final, so an unmark that lands first keeps the session.
        Then its history goes, a batch of steps at most a call, and its row
        once the history is gone, both under the purge login. A session
        marked within its retention, or never marked, is never taken: no
        purge runs on demand. Returns how many sessions it took up, so a
        whole batch says there may be more."""
        ...

    @abstractmethod
    async def purge_tenant(self, ctx: TenantContext) -> int:
        """The sweep, for one tenant past its own retention: every session,
        a batch at most a call, under the purge login. Any other tenant
        returns 0 and reads nothing."""
        ...
