import logging
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from uuid import UUID

from acme.om.agent_sessions.manager import AgentSessionsManagerInterface, SessionPurged
from acme.om.agent_sessions.rules import (
    announces,
    asks_for_run,
    held_private,
    lineage,
    parked_step,
    projected,
    purge_due,
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
from acme.om.attribution.rules import fold
from acme.om.attribution.types.principal import AgentRef
from acme.om.base import EMPTY_UUID, Platform, new_id, utcnow
from acme.om.context import Permission, TenantContext
from acme.om.exceptions import NotFound, PreconditionFailed, TenantMismatch, ValidationFailed
from acme.om.outbox import OutboxRelayInterface
from acme.om.outbox.types.row import OutboxRow, outbox_row, versioned_row
from acme.om.steps import StepsManagerInterface
from acme.om.steps.types.header import Park, ParkReason
from acme.om.steps.types.step import Step
from acme.om.tenancy import TenancyManagerInterface
from acme.om.work.types.work_item import (
    LoopPayload,
    WakeSessionPayload,
    WorkKind,
    work_row_kind,
)

log = logging.getLogger(__name__)

CREATED = "agent_sessions.agent_session.created"
UPDATED = "agent_sessions.agent_session.updated"
DELETED = "agent_sessions.agent_session.deleted"


class AgentSessionsOptions(Platform):
    max_limit: int = 50  # sessions one page holds at most
    project_batch: int = 200  # steps one read of the projection folds
    project_attempts: int = 3  # writers one projection reads again behind, at most
    purge_batch: int = 1000  # sessions one purge statement deletes at most
    wake_batch: int = 50  # parked sessions one read of a wake takes
    # How long a session marked deleted keeps its shape and can be unmarked;
    # past it the sweep purges it (ADR 1010).
    retention: timedelta = timedelta(days=30)
    # Sessions one purge across tenants, or of one tenant, takes up at most:
    # each brings its workspace and its records along, so far fewer than a
    # batch of rows fit in a sweep's budget.
    purge_sessions: int = 100


class AgentSessionsManagerImpl(AgentSessionsManagerInterface):
    def __init__(
        self,
        storage: AgentSessionStorageInterface,
        steps: StepsManagerInterface,
        tenancy: TenancyManagerInterface,
        relay: OutboxRelayInterface,
        options: AgentSessionsOptions,
        clock: Callable[[], datetime] = utcnow,
        *,
        purged: SessionPurged,
    ) -> None:
        self._purged = purged
        self._storage = storage
        self._steps = steps
        self._tenancy = tenancy
        self._relay = relay
        self._options = options
        self._clock = clock

    async def create_session(self, ctx: TenantContext, session: AgentSession) -> AgentSession:
        ctx.require(Permission.WRITE)
        source: AgentSession | None = None
        came_from = session.parent_id or session.handed_off_from
        if came_from is not None:
            found = await self._storage.read_session(ctx.org_id, came_from)
            if found is None or found.deleted_at is not None:
                raise ValidationFailed(f"no agent session {came_from} to come from")
            source = await self._at_head(ctx, found)
        now = self._clock()
        created = AgentSession.model_validate(
            {
                **session.model_dump(),
                **lineage(source, session),
                "created_at": now,
                "updated_at": now,
                "created_by": ctx.user_id,
                "updated_by": ctx.user_id,
                "status": SessionStatus.IDLE,
                "park": None,
                "status_seq": 0,
                "archived_at": None,
                "version": 1,
                "deleted_at": None,
                "deleted_by": None,
                "purge_started_at": None,
            }
        )
        rows = (versioned_row(ctx, CREATED, created.id, created.version),)
        if not await self._storage.create_session(ctx.org_id, created, rows):
            # A retry under the same id answers the session as stored.
            existing = await self._storage.read_session(ctx.org_id, created.id)
            if existing is None:
                raise TenantMismatch(f"agent session {created.id} is not in {ctx.org_id}")
            if existing.deleted_at is not None:
                raise NotFound(f"agent session {created.id} not found")
            return existing
        await self._relay_all(ctx, rows)
        return created

    async def get_session(self, ctx: TenantContext, session_id: UUID) -> AgentSession:
        ctx.require(Permission.READ)
        return await self._read(ctx, session_id)

    async def get_session_at_head(self, ctx: TenantContext, session_id: UUID) -> AgentSession:
        ctx.require(Permission.READ)
        return await self._at_head(ctx, await self._read(ctx, session_id))

    async def get_children(
        self, ctx: TenantContext, parent_id: UUID, after: UUID | None, limit: int
    ) -> AgentSessionPage:
        ctx.require(Permission.READ)
        limit = max(1, min(limit, self._options.max_limit))
        rows = await self._storage.read_children(ctx.org_id, parent_id, after, limit + 1)
        return AgentSessionPage(items=tuple(rows[:limit]), has_more=len(rows) > limit)

    async def get_ancestors(self, ctx: TenantContext, session_id: UUID) -> tuple[AgentRef, ...]:
        ctx.require(Permission.READ)
        above: list[AgentRef] = []
        parent_id = (await self._read(ctx, session_id)).parent_id
        while parent_id is not None:
            # As stored: one marked deleted still answers its kind.
            parent = await self._storage.read_session(ctx.org_id, parent_id)
            if parent is None:
                break
            above.append(
                AgentRef(kind=parent.kind, version=parent.kind_version, session_id=parent.id)
            )
            parent_id = parent.parent_id
        return tuple(above)

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
            if asks_for_run(session, after, page.items):
                rows += (loop_row(ctx, after),)
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

    async def receive(
        self, ctx: TenantContext, session_id: UUID, inputs: Sequence[Step]
    ) -> tuple[tuple[Step, ...], AgentSession]:
        ctx.require(Permission.WRITE)
        await self._read(ctx, session_id)
        stored = await self._steps.append_inputs(ctx, session_id, inputs)
        return stored, await self.project_status(ctx, session_id)

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
        _, unlocked = await self.receive(
            ctx, session.id, [unlock_step(new_id(), session.id, self._clock())]
        )
        return unlocked

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

    async def delete_session(self, ctx: TenantContext, session_id: UUID) -> AgentSession:
        ctx.require(Permission.WRITE)
        session = await self._read(ctx, session_id)
        if session.status is not SessionStatus.IDLE:
            raise ValidationFailed(f"agent session {session_id} is {session.status.value}")
        below = await self._open_below(ctx, session_id)
        if below is not None:
            raise ValidationFailed(
                f"agent session {session_id} has sub-agent {below.id} {below.status.value}"
            )
        now = self._clock()
        marked = AgentSession.model_validate(
            {
                **session.model_dump(),
                "deleted_at": now,
                "deleted_by": ctx.user_id,
                "version": session.version + 1,
                "updated_at": now,
                "updated_by": ctx.user_id,
            }
        )
        rows = (versioned_row(ctx, DELETED, marked.id, marked.version),)
        await self._storage.write_session(ctx.org_id, marked, session.version, rows)
        await self._relay_all(ctx, rows)
        return marked

    async def restore_session(self, ctx: TenantContext, session_id: UUID) -> AgentSession:
        ctx.require(Permission.WRITE)
        session = await self._storage.read_session(ctx.org_id, session_id)
        if session is None or session.purge_started_at is not None:
            raise NotFound(f"agent session {session_id} not found")
        if session.deleted_at is None:
            return session
        now = self._clock()
        restored = AgentSession.model_validate(
            {
                **session.model_dump(),
                "deleted_at": None,
                "deleted_by": None,
                "version": session.version + 1,
                "updated_at": now,
                "updated_by": ctx.user_id,
            }
        )
        rows = (versioned_row(ctx, UPDATED, restored.id, restored.version),)
        # Conditioned on the version read: a purge claim that lands first
        # moves it, and this write lands nothing.
        await self._storage.write_session(ctx.org_id, restored, session.version, rows)
        await self._relay_all(ctx, rows)
        return restored

    async def purge_across_tenants(self) -> int:
        now = self._clock()
        before = now - self._options.retention
        found = await self._storage.read_purgeable(before, self._options.purge_sessions)
        roots = {session.id: session.root_id for _, session in found}
        claimed: list[tuple[UUID, UUID]] = []
        for org_id, session in found:
            if not purge_due(session, before):
                continue
            if session.purge_started_at is None:
                # The claim is the platform's write, conditioned on the
                # version read: an unmark that landed since keeps the
                # session, and one that comes after it is refused.
                claim = AgentSession.model_validate(
                    {
                        **session.model_dump(),
                        "purge_started_at": now,
                        "version": session.version + 1,
                        "updated_at": now,
                        "updated_by": EMPTY_UUID,
                    }
                )
                try:
                    await self._storage.write_session(org_id, claim, session.version, ())
                except PreconditionFailed:
                    continue
            claimed.append((org_id, session.id))
        # The history first, then what other namespaces hold of the session,
        # then the row: a failure before the row leaves a claimed row, which
        # the next pass takes up again. The other order would leave steps, an
        # authority, or a tree no session names, which no pass would find. A
        # session whose holdings cannot go yet fails alone: it stays claimed,
        # and every other session of the pass is purged.
        for org_id, session_id in await self._steps.purge_histories(claimed):
            root_id = roots[session_id]
            try:
                alone = not await self._storage.tree_holds_others(org_id, root_id, session_id)
                await self._purged(org_id, session_id, root_id if alone else None)
                await self._storage.purge_session(org_id, session_id)
            except Exception:
                log.exception("agent session %s stays claimed: its purge failed", session_id)
        return len(found)

    async def purge_tenant(self, ctx: TenantContext) -> int:
        ctx.require(Permission.WRITE)
        if not await self._tenancy.tenant_expired(ctx):
            return 0
        found = await self._storage.read_tenant_sessions(ctx.org_id, self._options.purge_sessions)
        # What other namespaces hold of each session goes before its row, as
        # in a session's own purge: its workspace and its transport's records
        # have no purge of the tenant of their own. The tree goes with the
        # tenant's trees, so none is named. A session whose holdings cannot
        # go keeps its row, which the next pass reads again; the raise keeps
        # the tenant from being marked purged while one is left. The count
        # is of the sessions read, not of the rows deleted: a purge another
        # worker had in flight may have taken them, and a count of nothing
        # would mark the tenant while the rest of it remains.
        gone: list[UUID] = []
        for session_id in found:
            try:
                await self._purged(ctx.org_id, session_id, None)
            except Exception:
                log.exception("agent session %s keeps its row: its purge failed", session_id)
            else:
                gone.append(session_id)
        await self._storage.purge_tenant(ctx.org_id, gone)
        if len(gone) < len(found):
            raise RuntimeError(
                f"{len(found) - len(gone)} sessions of org {ctx.org_id} keep their rows: "
                "what they hold could not be purged"
            )
        return len(found)

    async def _open_below(self, ctx: TenantContext, session_id: UUID) -> AgentSession | None:
        """The first session below `session_id`, children and theirs, a level
        at a time, that has a loop open; the tree's count bounds the walk.
        One marked deleted is never answered, and the walk goes on below it.
        A check before the mark, not a lock: a sub-agent that wakes after it
        is still decided under the kind of every session above it
        (`get_ancestors`)."""
        parents = [session_id]
        while parents:
            parent_id = parents.pop(0)
            after: UUID | None = None
            while True:
                page = await self._storage.read_children(
                    ctx.org_id, parent_id, after, self._options.max_limit
                )
                for child in page:
                    if child.deleted_at is None and child.status is not SessionStatus.IDLE:
                        return child
                    parents.append(child.id)
                if len(page) < self._options.max_limit:
                    break
                after = page[-1].id
        return None

    async def _read(self, ctx: TenantContext, session_id: UUID) -> AgentSession:
        """A session every read may answer: one marked deleted is hidden, as
        one that never existed is."""
        session = await self._storage.read_session(ctx.org_id, session_id)
        if session is None or session.deleted_at is not None:
            raise NotFound(f"agent session {session_id} not found")
        return session

    async def _at_head(self, ctx: TenantContext, session: AgentSession) -> AgentSession:
        """`session` with its speaker, its mark, and whether it holds private
        data folded over the steps after its cache, page by page; nothing
        else changes."""
        speaker, marked, held = session.speaker, session.untrusted, session.holds_private
        after = session.status_seq
        while True:
            page = await self._steps.get_steps(ctx, session.id, after, self._options.project_batch)
            speaker, marked = fold(speaker, marked, page.items)
            held = held_private(held, page.items)
            if not page.has_more or not page.items:
                update = {"speaker": speaker, "untrusted": marked, "holds_private": held}
                return session.model_copy(update=update)
            after = page.items[-1].seq

    async def _relay_all(self, ctx: TenantContext, rows: tuple[OutboxRow, ...]) -> None:
        """The write has committed; a relay that fails is left to the sweep."""
        if rows:
            await self._relay.relay_all(ctx.org_id, rows)


def loop_row(ctx: TenantContext, session: AgentSession) -> OutboxRow:
    """The work row that asks for a run of the session's loop, written with
    the projection that made the session pending. It asks as the caller whose
    write woke the session: the claim rebuilds that principal under the
    service role, and every tool call asks its own principal again."""
    return outbox_row(
        ctx, work_row_kind(WorkKind.LOOP), session.id, LoopPayload().model_dump(mode="json")
    )


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
