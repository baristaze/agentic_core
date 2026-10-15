from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from pydantic import Field

from acme.om.agent_sessions.kinds import AgentSessionWorkKind, WakeAgentSessionsPayload
from acme.om.agent_steps.types.header import ParkReason
from acme.om.base import OmBase, utcnow
from acme.om.context import Permission, TenantContext
from acme.om.exceptions import NotFound, PreconditionFailed, TenantMismatch
from acme.om.outbox import OutboxRelayInterface
from acme.om.outbox.types.row import OutboxRow, outbox_row, versioned_row
from acme.om.spend_budgets.manager import SpendBudgetsManagerInterface
from acme.om.spend_budgets.rules import raises, window_bounds
from acme.om.spend_budgets.storage import LedgerStorageInterface, SpendBudgetStorageInterface
from acme.om.spend_budgets.types.amount import Amount
from acme.om.spend_budgets.types.spend_budget import (
    SpendBudget,
    SpendBudgetPage,
    SpendBudgetScopeKind,
    WindowKind,
)
from acme.om.spend_budgets.types.spend_hold import SpendTally
from acme.om.spend_budgets.types.usage import UsageRecord
from acme.om.tenancy import TenancyManagerInterface
from acme.om.work.types.work_item import work_row_kind

CREATED = "spend_budgets.spend_budget.created"
UPDATED = "spend_budgets.spend_budget.updated"

SETS_SPEND_BUDGETS = Permission.MANAGE_MEMBERS
"""A budget governs what the org's members may spend, so setting one takes
the permission that governs members."""


class SpendBudgetsOptions(OmBase):
    max_limit: int = 50  # budgets one page holds at most
    purge_batch: int = 1000  # the sweep's batch, which a report of what is left stays under
    # A close is a compare-and-set, tried again when an amount moved the
    # version between its read and its write.
    close_attempts: int = Field(default=3, ge=1)


class SpendBudgetsManagerImpl(SpendBudgetsManagerInterface):
    def __init__(
        self,
        storage: SpendBudgetStorageInterface,
        ledger: LedgerStorageInterface,
        tenancy: TenancyManagerInterface,
        relay: OutboxRelayInterface,
        options: SpendBudgetsOptions,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._storage = storage
        self._ledger = ledger
        self._tenancy = tenancy
        self._relay = relay
        self._options = options
        self._clock = clock

    async def create_spend_budget(self, ctx: TenantContext, budget: SpendBudget) -> SpendBudget:
        ctx.require(SETS_SPEND_BUDGETS)
        return await self._create(ctx, budget)

    async def cap_session(
        self, ctx: TenantContext, agent_session_id: UUID, spend_budget_id: UUID, amount: Amount
    ) -> SpendBudget:
        ctx.require(Permission.WRITE)
        now = self._clock()
        cap = SpendBudget(
            id=spend_budget_id,
            created_at=now,
            updated_at=now,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
            scope_kind=SpendBudgetScopeKind.AGENT_SESSION,
            scope_key=str(agent_session_id),
            window_kind=WindowKind.LIFE,
            cost_micros=amount.cost_micros,
            tokens=amount.tokens,
        )
        return await self._create(ctx, cap)

    async def _create(self, ctx: TenantContext, budget: SpendBudget) -> SpendBudget:
        now = self._clock()
        created = SpendBudget.model_validate(
            {
                **budget.model_dump(),
                "created_at": now,
                "updated_at": now,
                "created_by": ctx.user_id,
                "updated_by": ctx.user_id,
                "version": 1,
                "closed_at": None,  # only a close sets it
            }
        )
        rows = (versioned_row(ctx, CREATED, created.id, created.version),)
        if not await self._storage.create_spend_budget(ctx.org_id, created, rows):
            # A retry under the same id answers the budget as stored.
            existing = await self._storage.read_spend_budget(ctx.org_id, created.id)
            if existing is None:
                raise TenantMismatch(f"budget {created.id} is not in {ctx.org_id}")
            return existing
        await self._relay_all(ctx, rows)
        return created

    async def get_spend_budget(self, ctx: TenantContext, spend_budget_id: UUID) -> SpendBudget:
        ctx.require(Permission.READ)
        return await self._read(ctx, spend_budget_id)

    async def get_spend_budgets(
        self, ctx: TenantContext, after: UUID | None, limit: int
    ) -> SpendBudgetPage:
        ctx.require(Permission.READ)
        limit = max(1, min(limit, self._options.max_limit))
        rows = await self._storage.read_spend_budgets(ctx.org_id, after, limit + 1)
        return SpendBudgetPage(items=tuple(rows[:limit]), has_more=len(rows) > limit)

    async def change_amount(
        self, ctx: TenantContext, spend_budget_id: UUID, amount: Amount, expected_version: int
    ) -> SpendBudget:
        ctx.require(SETS_SPEND_BUDGETS)
        stored = await self._read(ctx, spend_budget_id)
        now = self._clock()
        changed = SpendBudget.model_validate(
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
            wake = WakeAgentSessionsPayload(reason=ParkReason.SPEND_BUDGET)
            rows += (
                outbox_row(
                    ctx,
                    work_row_kind(AgentSessionWorkKind.WAKE_AGENT_SESSIONS),
                    ctx.org_id,
                    wake.model_dump(mode="json", exclude_none=True),
                ),
            )
        await self._storage.write_spend_budget(ctx.org_id, changed, expected_version, rows)
        await self._relay_all(ctx, rows)
        return changed

    async def close_spend_budget(self, ctx: TenantContext, spend_budget_id: UUID) -> SpendBudget:
        ctx.require(SETS_SPEND_BUDGETS)
        for _ in range(self._options.close_attempts - 1):
            try:
                return await self._close(ctx, spend_budget_id)
            except PreconditionFailed:
                # An amount changed between the read and the write: close the
                # budget as it stands now, so the change never reopens it.
                continue
        return await self._close(ctx, spend_budget_id)

    async def _close(self, ctx: TenantContext, spend_budget_id: UUID) -> SpendBudget:
        stored = await self._read(ctx, spend_budget_id)
        if stored.closed_at is not None:
            return stored  # closed once: the first close stands
        now = self._clock()
        closed = SpendBudget.model_validate(
            {
                **stored.model_dump(),
                "closed_at": now,
                "version": stored.version + 1,
                "updated_at": now,
                "updated_by": ctx.user_id,
            }
        )
        # A closed budget binds nothing, so a session it parked can go on:
        # the ones whose park names it are woken, and ask again.
        wake = WakeAgentSessionsPayload(reason=ParkReason.SPEND_BUDGET, unlock=str(closed.id))
        rows = (
            versioned_row(ctx, UPDATED, closed.id, closed.version),
            outbox_row(
                ctx,
                work_row_kind(AgentSessionWorkKind.WAKE_AGENT_SESSIONS),
                ctx.org_id,
                wake.model_dump(mode="json"),
            ),
        )
        await self._storage.write_spend_budget(ctx.org_id, closed, stored.version, rows)
        await self._relay_all(ctx, rows)
        return closed

    async def get_spend(self, ctx: TenantContext, spend_budget_id: UUID) -> SpendTally:
        ctx.require(Permission.READ)
        budget = await self._read(ctx, spend_budget_id)
        start, _ = window_bounds(budget.window, self._clock())
        tally = await self._ledger.read_spend_tally(ctx.org_id, budget.id, start)
        return tally or SpendTally(spend_budget_id=budget.id, window_start=start)

    async def record_usage(self, ctx: TenantContext, record: UsageRecord) -> bool:
        ctx.require(Permission.WRITE)
        return await self._ledger.append_usage_record(ctx.org_id, record)

    async def purge_tenant(self, ctx: TenantContext) -> int:
        ctx.require(Permission.WRITE)
        if not await self._tenancy.tenant_expired(ctx):
            return 0
        return await self._storage.purge_tenant(ctx.org_id, self._options.purge_batch)

    async def _read(self, ctx: TenantContext, spend_budget_id: UUID) -> SpendBudget:
        budget = await self._storage.read_spend_budget(ctx.org_id, spend_budget_id)
        if budget is None:
            raise NotFound(f"budget {spend_budget_id} not found")
        return budget

    async def _relay_all(self, ctx: TenantContext, rows: tuple[OutboxRow, ...]) -> None:
        """The write has committed; a relay that fails is left to the sweep."""
        await self._relay.relay_all(ctx.org_id, rows)
