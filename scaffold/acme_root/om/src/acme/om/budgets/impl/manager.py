from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from acme.om.base import Platform, utcnow
from acme.om.budgets.manager import BudgetsManagerInterface
from acme.om.budgets.rules import raises, window_bounds
from acme.om.budgets.storage import BudgetStorageInterface, LedgerStorageInterface
from acme.om.budgets.types.amount import Amount
from acme.om.budgets.types.budget import Budget, BudgetPage
from acme.om.budgets.types.hold import Tally
from acme.om.budgets.types.usage import UsageRecord
from acme.om.context import Permission, TenantContext
from acme.om.exceptions import NotFound, TenantMismatch
from acme.om.outbox import OutboxRelayInterface
from acme.om.outbox.types.row import OutboxRow, outbox_row, versioned_row
from acme.om.steps.types.header import ParkReason
from acme.om.tenancy import TenancyManagerInterface
from acme.om.work.types.work_item import WakeSessionsPayload, WorkKind, work_row_kind

CREATED = "budgets.budget.created"
UPDATED = "budgets.budget.updated"

SETS_BUDGETS = Permission.MANAGE_MEMBERS
"""A budget governs what the org's members may spend, so setting one takes
the permission that governs members."""


class BudgetsOptions(Platform):
    max_limit: int = 50  # budgets one page holds at most
    purge_batch: int = 1000  # the sweep's batch, which a report of what is left stays under


class BudgetsManagerImpl(BudgetsManagerInterface):
    def __init__(
        self,
        storage: BudgetStorageInterface,
        ledger: LedgerStorageInterface,
        tenancy: TenancyManagerInterface,
        relay: OutboxRelayInterface,
        options: BudgetsOptions,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._storage = storage
        self._ledger = ledger
        self._tenancy = tenancy
        self._relay = relay
        self._options = options
        self._clock = clock

    async def create_budget(self, ctx: TenantContext, budget: Budget) -> Budget:
        ctx.require(SETS_BUDGETS)
        now = self._clock()
        created = Budget.model_validate(
            {
                **budget.model_dump(),
                "created_at": now,
                "updated_at": now,
                "created_by": ctx.user_id,
                "updated_by": ctx.user_id,
                "version": 1,
            }
        )
        rows = (versioned_row(ctx, CREATED, created.id, created.version),)
        if not await self._storage.create_budget(ctx.org_id, created, rows):
            # A retry under the same id answers the budget as stored.
            existing = await self._storage.read_budget(ctx.org_id, created.id)
            if existing is None:
                raise TenantMismatch(f"budget {created.id} is not in {ctx.org_id}")
            return existing
        await self._relay_all(ctx, rows)
        return created

    async def get_budget(self, ctx: TenantContext, budget_id: UUID) -> Budget:
        ctx.require(Permission.READ)
        return await self._read(ctx, budget_id)

    async def get_budgets(self, ctx: TenantContext, after: UUID | None, limit: int) -> BudgetPage:
        ctx.require(Permission.READ)
        limit = max(1, min(limit, self._options.max_limit))
        rows = await self._storage.read_budgets(ctx.org_id, after, limit + 1)
        return BudgetPage(items=tuple(rows[:limit]), has_more=len(rows) > limit)

    async def change_amount(
        self, ctx: TenantContext, budget_id: UUID, amount: Amount, expected_version: int
    ) -> Budget:
        ctx.require(SETS_BUDGETS)
        stored = await self._read(ctx, budget_id)
        now = self._clock()
        changed = Budget.model_validate(
            {
                **stored.model_dump(),
                "cost_micros": amount.cost_micros,
                "tokens": amount.tokens,
                "version": expected_version + 1,
                "updated_at": now,
                "updated_by": ctx.user_id,
            }
        )
        rows: tuple[OutboxRow, ...] = (versioned_row(ctx, UPDATED, changed.id, changed.version),)
        if raises(stored.amount, amount):
            # Raising a budget is the instruction to continue: every session
            # of the org parked on a budget is woken, and its gate runs again.
            wake = WakeSessionsPayload(reason=ParkReason.BUDGET)
            rows += (
                outbox_row(
                    ctx,
                    work_row_kind(WorkKind.WAKE_SESSIONS),
                    ctx.org_id,
                    wake.model_dump(mode="json"),
                ),
            )
        await self._storage.write_budget(ctx.org_id, changed, expected_version, rows)
        await self._relay_all(ctx, rows)
        return changed

    async def get_spend(self, ctx: TenantContext, budget_id: UUID) -> Tally:
        ctx.require(Permission.READ)
        budget = await self._read(ctx, budget_id)
        start, _ = window_bounds(budget.window, self._clock())
        tally = await self._ledger.read_tally(ctx.org_id, budget.id, start)
        return tally or Tally(budget_id=budget.id, window_start=start)

    async def record_usage(self, ctx: TenantContext, record: UsageRecord) -> bool:
        ctx.require(Permission.WRITE)
        return await self._ledger.append_usage_record(ctx.org_id, record)

    async def purge_tenant(self, ctx: TenantContext) -> int:
        ctx.require(Permission.WRITE)
        if not await self._tenancy.tenant_expired(ctx):
            return 0
        return await self._storage.purge_tenant(ctx.org_id, self._options.purge_batch)

    async def purge_ledger(self, ctx: TenantContext) -> int:
        ctx.require(Permission.WRITE)
        if not await self._tenancy.tenant_expired(ctx):
            return 0
        return await self._ledger.count_tenant(ctx.org_id, max(1, self._options.purge_batch - 1))

    async def _read(self, ctx: TenantContext, budget_id: UUID) -> Budget:
        budget = await self._storage.read_budget(ctx.org_id, budget_id)
        if budget is None:
            raise NotFound(f"budget {budget_id} not found")
        return budget

    async def _relay_all(self, ctx: TenantContext, rows: tuple[OutboxRow, ...]) -> None:
        """The write has committed; a relay that fails is left to the sweep."""
        await self._relay.relay_all(ctx.org_id, rows)
