"""The agent sessions swimlane: sessions, each a series of loops over one
history, and the status each caches from its steps.

The steps are the truth. A step lands in the history first; the session's
cached status follows it in a write of its own, here, with the outbox row
that announces a change. That write may be late or lost, so it reads the
steps from where it last stopped and is always rebuildable from them."""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from uuid import UUID

from acme.om.agent_sessions.types.agent_session import (
    AgentSession,
    AgentSessionPage,
    SessionStatus,
)
from acme.om.context import TenantContext
from acme.om.steps.types.header import Park, ParkReason

SessionPurged = Callable[[UUID, UUID, UUID | None], Awaitable[None]]
"""What other namespaces hold of a session, purged before its row: given its
tenant, its id, and its tree's id when no other session of the tree is
left, and None otherwise. The root binds it to attribution's and the agents'
purges, which run under the purge login in that tenant."""


class AgentSessionsManagerInterface(ABC):
    @abstractmethod
    async def create_session(self, ctx: TenantContext, session: AgentSession) -> AgentSession:
        """The create: the session lands idle, with no history yet, and is
        announced. A session with a parent joins its parent's tree, and one
        handed over roots a tree of its own; a session to come from that the
        tenant does not hold is `ValidationFailed`. What a session takes from
        where it came is the manager's, read from that session's history as
        it stands (`agent_sessions.rules.lineage`): its mark, and for a child
        the cut of its tools, so no maker grants a child more than its parent
        holds. The root, the depth, the status, and the provenance are the
        manager's too. An id written already answers the session as
        stored."""
        ...

    @abstractmethod
    async def get_session(self, ctx: TenantContext, session_id: UUID) -> AgentSession:
        """A session of the tenant; one another tenant holds, or one marked
        deleted, is `NotFound`, as one that never existed is."""
        ...

    @abstractmethod
    async def get_session_at_head(self, ctx: TenantContext, session_id: UUID) -> AgentSession:
        """The session with its speaker and its mark folded up to the head of
        its history: the cache, then the steps after it. Nothing is written,
        and the status and the version stay the cache's. What attribution
        answers is read from it."""
        ...

    @abstractmethod
    async def get_children(
        self, ctx: TenantContext, parent_id: UUID, after: UUID | None, limit: int
    ) -> AgentSessionPage:
        """One page of the sessions `parent_id` spawned, by id, strictly
        after `after`; `limit` is clamped."""
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
    async def park(
        self, ctx: TenantContext, session_id: UUID, epoch: int, loop_id: UUID, park: Park
    ) -> AgentSession:
        """A run's loop parks: a `parked` step carrying the park is appended
        under the run's epoch (`StaleWriter` once another run holds the
        session), and the status follows it. A park with a retry time lands,
        with the session's write, the work that wakes it at that time."""
        ...

    @abstractmethod
    async def resume(
        self, ctx: TenantContext, session_id: UUID, epoch: int, loop_id: UUID
    ) -> AgentSession:
        """A new run takes up a loop whose unlock happened: a `resumed` step
        is appended under the run's epoch, and the session is running. The
        run's gates run again before its next call: a woken loop is not
        trusted."""
        ...

    @abstractmethod
    async def wake_session(self, ctx: TenantContext, session_id: UUID, park: Park) -> AgentSession:
        """A park's retry time came: a session still parked on exactly `park`
        is unlocked by an `unlock` control the engine writes, and is pending
        for a run to take up. A session that moved on is answered as it is."""
        ...

    @abstractmethod
    async def wake_parked(self, ctx: TenantContext, reason: ParkReason) -> int:
        """The reason the org's sessions parked for is gone, as when a budget
        is raised: every session parked for it is unlocked, and each one's
        gates run again when it resumes. Returns how many."""
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
        Then its history goes, a batch of steps at most a call, and once the
        history is gone, its authority, its tree when it is the tree's last
        session, and its row, all under the purge login. A session
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
