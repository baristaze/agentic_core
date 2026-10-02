from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from acme.om.agent_sessions.manager import AgentSessionsManagerInterface
from acme.om.agent_sessions.rules import (
    announces,
    parked_step,
    projected,
    resumed_step,
    unlock_step,
    wakes_at,
)
from acme.om.agent_sessions.storage import AgentSessionStorageInterface
from acme.om.agent_sessions.types.agent_session import (
    AgentSession,
    AgentSessionPage,
    SessionStatus,
)
from acme.om.base import Platform, new_id, utcnow
from acme.om.context import Permission, TenantContext
from acme.om.exceptions import NotFound, PreconditionFailed, TenantMismatch, ValidationFailed
from acme.om.outbox import OutboxRelayInterface
from acme.om.outbox.types.row import OutboxRow, outbox_row, versioned_row
from acme.om.steps import StepsManagerInterface
from acme.om.steps.types.header import Park, ParkReason
from acme.om.tenancy import TenancyManagerInterface
from acme.om.work.types.work_item import WakeSessionPayload, WorkKind, work_row_kind

CREATED = "agent_sessions.agent_session.created"
UPDATED = "agent_sessions.agent_session.updated"


class AgentSessionsOptions(Platform):
    max_limit: int = 50  # sessions one page holds at most
    project_batch: int = 200  # steps one read of the projection folds
    project_attempts: int = 3  # writers one projection reads again behind, at most
    purge_batch: int = 1000  # sessions one purge statement deletes at most
    wake_batch: int = 50  # parked sessions one read of a wake takes


class AgentSessionsManagerImpl(AgentSessionsManagerInterface):
    def __init__(
        self,
        storage: AgentSessionStorageInterface,
        steps: StepsManagerInterface,
        tenancy: TenancyManagerInterface,
        relay: OutboxRelayInterface,
        options: AgentSessionsOptions,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._storage = storage
        self._steps = steps
        self._tenancy = tenancy
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
            waiting = wakes_at(session, after, page.items)
            if waiting is not None:
                rows += (wake_row(ctx, after, waiting),)
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

    async def park(
        self, ctx: TenantContext, session_id: UUID, epoch: int, loop_id: UUID, park: Park
    ) -> AgentSession:
        ctx.require(Permission.WRITE)
        step = parked_step(new_id(), session_id, loop_id, park, self._clock())
        await self._steps.append_steps(ctx, session_id, epoch, [step])
        return await self.project_status(ctx, session_id)

    async def resume(
        self, ctx: TenantContext, session_id: UUID, epoch: int, loop_id: UUID
    ) -> AgentSession:
        ctx.require(Permission.WRITE)
        step = resumed_step(new_id(), session_id, loop_id, self._clock())
        await self._steps.append_steps(ctx, session_id, epoch, [step])
        return await self.project_status(ctx, session_id)

    async def wake_session(self, ctx: TenantContext, session_id: UUID, park: Park) -> AgentSession:
        ctx.require(Permission.WRITE)
        session = await self._read(ctx, session_id)
        if session.status is not SessionStatus.PARKED or session.park != park:
            return session
        return await self._unlock(ctx, session)

    async def wake_parked(self, ctx: TenantContext, reason: ParkReason) -> int:
        ctx.require(Permission.WRITE)
        woken = 0
        after: UUID | None = None
        while True:
            batch = self._options.wake_batch
            page = await self._storage.read_sessions(ctx.org_id, SessionStatus.PARKED, after, batch)
            for session in page:
                if session.park is None or session.park.reason is not reason:
                    continue
                try:
                    await self._unlock(ctx, session)
                except PreconditionFailed:
                    continue  # another writer moved it; its gates run when it resumes
                woken += 1
            if len(page) < batch:
                return woken
            after = page[-1].id

    async def _unlock(self, ctx: TenantContext, session: AgentSession) -> AgentSession:
        """The engine's unlock, through the inbox, and the status after it. A
        park that changed meanwhile is unlocked too, which costs one more
        check: the run that takes it up asks its gates again."""
        await self._steps.append_inputs(
            ctx, session.id, [unlock_step(new_id(), session.id, self._clock())]
        )
        return await self.project_status(ctx, session.id)

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

    async def purge_tenant(self, ctx: TenantContext) -> int:
        ctx.require(Permission.WRITE)
        if not await self._tenancy.tenant_expired(ctx):
            return 0
        return await self._storage.purge_tenant(ctx.org_id, self._options.purge_batch)

    async def _read(self, ctx: TenantContext, session_id: UUID) -> AgentSession:
        session = await self._storage.read_session(ctx.org_id, session_id)
        if session is None:
            raise NotFound(f"agent session {session_id} not found")
        return session

    async def _relay_all(self, ctx: TenantContext, rows: tuple[OutboxRow, ...]) -> None:
        """The write has committed; a relay that fails is left to the sweep."""
        if rows:
            await self._relay.relay_all(ctx.org_id, rows)


def wake_row(ctx: TenantContext, session: AgentSession, park: Park) -> OutboxRow:
    """The work row that wakes a session at its park's retry time: the queue
    holds it until then, and no timer does. It asks as the person who made
    the session, whoever wrote the park, so the wake runs as them."""
    payload = WakeSessionPayload(not_before=park.retry_at or session.updated_at, park=park)
    return outbox_row(
        ctx,
        work_row_kind(WorkKind.WAKE_SESSION),
        session.id,
        payload.model_dump(mode="json"),
    ).model_copy(update={"actor_id": session.created_by})
