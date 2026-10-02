from collections.abc import Sequence
from uuid import UUID

from acme.om.base import Platform
from acme.om.context import Permission, TenantContext
from acme.om.exceptions import ValidationFailed
from acme.om.steps.manager import StepsManagerInterface
from acme.om.steps.storage import StepStorageInterface
from acme.om.steps.types.page import StepCursor, StepPage
from acme.om.steps.types.step import Step
from acme.om.tenancy import TenancyManagerInterface


class StepsOptions(Platform):
    max_limit: int = 200  # steps one page holds at most
    max_append: int = 100  # steps one append writes at most
    purge_batch: int = 1000  # the sweep's batch, which a report of what is left stays under


class StepsManagerImpl(StepsManagerInterface):
    def __init__(
        self,
        storage: StepStorageInterface,
        tenancy: TenancyManagerInterface,
        options: StepsOptions,
    ) -> None:
        self._storage = storage
        self._tenancy = tenancy
        self._options = options

    async def begin_run(self, ctx: TenantContext, session_id: UUID) -> int:
        ctx.require(Permission.WRITE)
        return await self._storage.begin_run(ctx.org_id, session_id)

    async def append_steps(
        self, ctx: TenantContext, session_id: UUID, epoch: int, steps: Sequence[Step]
    ) -> tuple[Step, ...]:
        ctx.require(Permission.WRITE)
        self._bound(steps)
        return await self._storage.append_steps(ctx.org_id, session_id, epoch, steps)

    async def append_inputs(
        self, ctx: TenantContext, session_id: UUID, steps: Sequence[Step]
    ) -> tuple[Step, ...]:
        ctx.require(Permission.WRITE)
        self._bound(steps)
        return await self._storage.append_inputs(ctx.org_id, session_id, steps)

    async def get_steps(
        self, ctx: TenantContext, session_id: UUID, after_seq: int, limit: int
    ) -> StepPage:
        ctx.require(Permission.READ)
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
        return await self._storage.count_tenant(ctx.org_id, max(1, self._options.purge_batch - 1))

    def _bound(self, steps: Sequence[Step]) -> None:
        if len(steps) > self._options.max_append:
            raise ValidationFailed(f"an append holds at most {self._options.max_append} steps")
