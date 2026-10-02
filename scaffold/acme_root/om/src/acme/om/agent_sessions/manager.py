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
        announced. A session with a parent joins its parent's tree, and one
        handed over roots a tree of its own; a session to come from that the
        tenant does not hold is `ValidationFailed`. What a session takes from
        where it came is the manager's, read from that session's history as
        it stands (`agent_sessions.rules.lineage`): its mark, the principal
        its calls run under, and for a child its spender and the cut of its
        tools, so no maker grants a child more than its parent holds. The
        root, the depth, the status, and the provenance are the manager's
        too. An id written already answers the session as stored."""
        ...

    @abstractmethod
    async def get_session(self, ctx: TenantContext, session_id: UUID) -> AgentSession:
        """A session of the tenant; one another tenant holds is `NotFound`,
        as one that never existed is."""
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
    async def assign_principal(self, ctx: TenantContext, session_id: UUID) -> AgentSession:
        """The caller takes a session over: its tool calls run under them
        from here. A steady session whose principal no longer holds parks
        until a person does this. A child's principal is its parent's, and a
        child is never taken over (`ValidationFailed`)."""
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
