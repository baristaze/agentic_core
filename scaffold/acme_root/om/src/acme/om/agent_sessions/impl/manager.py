from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from acme.om.agent_sessions.manager import AgentSessionsManagerInterface
from acme.om.agent_sessions.rules import announces, projected
from acme.om.agent_sessions.storage import AgentSessionStorageInterface
from acme.om.agent_sessions.types.agent_session import (
    AgentSession,
    AgentSessionPage,
    SessionStatus,
)
from acme.om.base import Platform, utcnow
from acme.om.context import Permission, TenantContext
from acme.om.exceptions import NotFound, PreconditionFailed, TenantMismatch, ValidationFailed
from acme.om.outbox import OutboxRelayInterface
from acme.om.outbox.types.row import OutboxRow, versioned_row
from acme.om.steps import StepsManagerInterface

CREATED = "agent_sessions.agent_session.created"
UPDATED = "agent_sessions.agent_session.updated"


class AgentSessionsOptions(Platform):
    max_limit: int = 50  # sessions one page holds at most
    project_batch: int = 200  # steps one read of the projection folds
    project_attempts: int = 3  # writers one projection reads again behind, at most


class AgentSessionsManagerImpl(AgentSessionsManagerInterface):
    def __init__(
        self,
        storage: AgentSessionStorageInterface,
        steps: StepsManagerInterface,
        relay: OutboxRelayInterface,
        options: AgentSessionsOptions,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._storage = storage
        self._steps = steps
        self._relay = relay
        self._options = options
        self._clock = clock

    async def create_session(self, ctx: TenantContext, session: AgentSession) -> AgentSession:
        ctx.require(Permission.WRITE)
        root_id = session.id
        if session.parent_id is not None:
            parent = await self._storage.read_session(ctx.org_id, session.parent_id)
            if parent is None:
                raise ValidationFailed(f"no parent session {session.parent_id}")
            root_id = parent.root_id
        now = self._clock()
        created = AgentSession.model_validate(
            {
                **session.model_dump(),
                "created_at": now,
                "updated_at": now,
                "created_by": ctx.user_id,
                "updated_by": ctx.user_id,
                "root_id": root_id,
                "status": SessionStatus.IDLE,
                "park": None,
                "status_seq": 0,
                "archived_at": None,
                "version": 1,
            }
        )
        rows = (versioned_row(ctx, CREATED, created.id, created.version),)
        if not await self._storage.create_session(ctx.org_id, created, rows):
            # A retry under the same id answers the session as stored.
            existing = await self._storage.read_session(ctx.org_id, created.id)
            if existing is None:
                raise TenantMismatch(f"agent session {created.id} is not in {ctx.org_id}")
            return existing
        await self._relay_all(ctx, rows)
        return created

    async def get_session(self, ctx: TenantContext, session_id: UUID) -> AgentSession:
        ctx.require(Permission.READ)
        return await self._read(ctx, session_id)

    async def get_sessions(
        self,
        ctx: TenantContext,
        status: SessionStatus | None,
        after: UUID | None,
        limit: int,
    ) -> AgentSessionPage:
        ctx.require(Permission.READ)
        limit = max(1, min(limit, self._options.max_limit))
        rows = await self._storage.read_sessions(ctx.org_id, status, after, limit + 1)
        return AgentSessionPage(items=tuple(rows[:limit]), has_more=len(rows) > limit)

    async def project_status(self, ctx: TenantContext, session_id: UUID) -> AgentSession:
        ctx.require(Permission.WRITE)
        session = await self._read(ctx, session_id)
        behind = 0
        while True:
            page = await self._steps.get_steps(
                ctx, session_id, session.status_seq, self._options.project_batch
            )
            after = projected(session, page.items, self._clock(), ctx.user_id)
            if after is session:
                return session
            rows: tuple[OutboxRow, ...] = ()
            if announces(session, after, page.items):
                rows = (versioned_row(ctx, UPDATED, after.id, after.version),)
            try:
                await self._storage.write_session(ctx.org_id, after, session.version, rows)
            except PreconditionFailed:
                # Another projection, or another write, landed first: read
                # what it left and fold on from there.
                behind += 1
                if behind >= self._options.project_attempts:
                    raise
                session = await self._read(ctx, session_id)
                continue
            await self._relay_all(ctx, rows)
            session = after
            if not page.has_more:
                return session

    async def archive_session(self, ctx: TenantContext, session_id: UUID) -> AgentSession:
        ctx.require(Permission.WRITE)
        session = await self._read(ctx, session_id)
        if session.archived_at is not None:
            return session
        if session.status is not SessionStatus.IDLE:
            raise ValidationFailed(f"agent session {session_id} is {session.status.value}")
        now = self._clock()
        archived = AgentSession.model_validate(
            {
                **session.model_dump(),
                "archived_at": now,
                "version": session.version + 1,
                "updated_at": now,
                "updated_by": ctx.user_id,
            }
        )
        rows = (versioned_row(ctx, UPDATED, archived.id, archived.version),)
        await self._storage.write_session(ctx.org_id, archived, session.version, rows)
        await self._relay_all(ctx, rows)
        return archived

    async def _read(self, ctx: TenantContext, session_id: UUID) -> AgentSession:
        session = await self._storage.read_session(ctx.org_id, session_id)
        if session is None:
            raise NotFound(f"agent session {session_id} not found")
        return session

    async def _relay_all(self, ctx: TenantContext, rows: tuple[OutboxRow, ...]) -> None:
        """The write has committed; a relay that fails is left to the sweep."""
        if rows:
            await self._relay.relay_all(ctx.org_id, rows)
