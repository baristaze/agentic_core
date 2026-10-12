from collections.abc import Sequence
from uuid import UUID

from acme.om.attribution.rules import (
    decided_by,
    instructs,
    person_written,
    principal_of,
    said_by,
    switched_by,
)
from acme.om.base import Platform
from acme.om.context import Permission, TenantContext
from acme.om.exceptions import NotAuthorized, ValidationFailed
from acme.om.steps.manager import InstructCheck, OpenCheck, SteerCheck, StepsManagerInterface
from acme.om.steps.storage import StepStorageInterface
from acme.om.steps.types.page import StepCursor, StepPage
from acme.om.steps.types.step import Step, switches_standing
from acme.om.tenancy import TenancyManagerInterface


class StepsOptions(Platform):
    max_limit: int = 200  # steps one page holds at most
    max_append: int = 100  # steps one append writes at most
    purge_batch: int = 1000  # steps one purge statement deletes at most


async def no_registry(ctx: TenantContext, session_id: UUID) -> None:
    """The check of a history no agent reads: no session over it offers a
    tool, so anyone who may write it may speak in it."""


async def no_steerer(ctx: TenantContext, session_id: UUID) -> bool:
    """The check of a history no session stands over: whoever may write it
    steers it."""
    return True


async def no_session(ctx: TenantContext, session_id: UUID) -> None:
    """The check of a history no session stands over: whoever may read it
    opens it."""


class StepsManagerImpl(StepsManagerInterface):
    def __init__(
        self,
        storage: StepStorageInterface,
        tenancy: TenancyManagerInterface,
        options: StepsOptions,
        *,
        instructs: InstructCheck,
        steers: SteerCheck,
        opens: OpenCheck,
    ) -> None:
        self._storage = storage
        self._tenancy = tenancy
        self._options = options
        self._instructs = instructs
        self._steers = steers
        self._opens = opens

    async def begin_run(self, ctx: TenantContext, session_id: UUID) -> int:
        ctx.require(Permission.WRITE)
        return await self._storage.begin_run(ctx.org_id, session_id)

    async def append_steps(
        self, ctx: TenantContext, session_id: UUID, epoch: int, steps: Sequence[Step]
    ) -> tuple[Step, ...]:
        ctx.require(Permission.WRITE)
        self._bound(steps)
        said = await self._said(ctx, session_id, steps)
        await self._asked(ctx, session_id, said)
        return await self._storage.append_steps(ctx.org_id, session_id, epoch, said)

    async def append_inputs(
        self, ctx: TenantContext, session_id: UUID, steps: Sequence[Step]
    ) -> tuple[Step, ...]:
        ctx.require(Permission.WRITE)
        self._bound(steps)
        said = await self._said(ctx, session_id, steps)
        await self._asked(ctx, session_id, said)
        return await self._storage.append_inputs(ctx.org_id, session_id, said)

    async def get_steps(
        self, ctx: TenantContext, session_id: UUID, after_seq: int, limit: int
    ) -> StepPage:
        ctx.require(Permission.READ)
        if not ctx.has(Permission.MANAGE_SESSIONS):
            await self._opens(ctx, session_id)
        limit = max(1, min(limit, self._options.max_limit))
        rows = await self._storage.read_steps(ctx.org_id, session_id, max(0, after_seq), limit + 1)
        return StepPage(items=tuple(rows[:limit]), has_more=len(rows) > limit)

    async def get_cursor(self, ctx: TenantContext, session_id: UUID) -> StepCursor:
        ctx.require(Permission.READ)
        return await self._storage.read_cursor(ctx.org_id, session_id)

    async def purge_tenant(self, ctx: TenantContext) -> int:
        ctx.require(Permission.WRITE)
        if not await self._tenancy.tenant_expired(ctx):
            return 0
        return await self._storage.purge_tenant(ctx.org_id, self._options.purge_batch)

    async def purge_histories(
        self, sessions: Sequence[tuple[UUID, UUID]]
    ) -> list[tuple[UUID, UUID]]:
        batch = self._options.purge_batch
        gone: list[tuple[UUID, UUID]] = []
        for org_id, session_id in sessions:
            if await self._storage.purge_history(org_id, session_id, batch) < batch:
                gone.append((org_id, session_id))
        return gone

    async def _asked(self, ctx: TenantContext, session_id: UUID, steps: Sequence[Step]) -> None:
        """An append that holds an instruction asks first whether its sender
        may instruct the session, whichever append carries it."""
        if any(instructs(step) for step in steps):
            await self._instructs(ctx, session_id)

    async def _said(
        self, ctx: TenantContext, session_id: UUID, steps: Sequence[Step]
    ) -> list[Step]:
        """Each step in the name of the context that appends it: a person's
        message is its user's, the steerer's an instruction and a
        participant's data; a decision its user's, in its role; and a switch
        of the steerer or of who sees the session its user's. Only the
        steerer pins standing text."""
        speaker = principal_of(ctx)
        steers = True
        if any(person_written(step) or switches_standing(step) for step in steps):
            steers = await self._steers(ctx, session_id)
        if not steers and any(switches_standing(step) for step in steps):
            raise NotAuthorized(f"only the steerer of agent session {session_id} pins its text")
        return [
            switched_by(
                decided_by(said_by(step, speaker, steers=steers), ctx.user_id, ctx.role), speaker
            )
            for step in steps
        ]

    def _bound(self, steps: Sequence[Step]) -> None:
        if len(steps) > self._options.max_append:
            raise ValidationFailed(f"an append holds at most {self._options.max_append} steps")
