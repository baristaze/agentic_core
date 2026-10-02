"""The budgets swimlane: what may be spent, and the gate every model call and
every spending job passes before it starts.

A budget has a scope, a window, and an amount, and binds every call charged
to its scope. Setting one governs what the org's members may spend, so it
takes the permission that governs members; reading one takes READ. The gate
is `BudgetGateInterface`, beside this one."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.budgets.types.amount import Amount
from acme.om.budgets.types.budget import Budget, BudgetPage
from acme.om.budgets.types.hold import Tally
from acme.om.context import TenantContext


class BudgetsManagerInterface(ABC):
    @abstractmethod
    async def create_budget(self, ctx: TenantContext, budget: Budget) -> Budget:
        """The create, announced. The version and the provenance are the
        manager's. An id written already answers the budget as stored."""
        ...

    @abstractmethod
    async def get_budget(self, ctx: TenantContext, budget_id: UUID) -> Budget:
        """A budget of the tenant; one another tenant holds is `NotFound`."""
        ...

    @abstractmethod
    async def get_budgets(self, ctx: TenantContext, after: UUID | None, limit: int) -> BudgetPage:
        """One page of the tenant's budgets by id, strictly after `after`;
        `limit` is clamped."""
        ...

    @abstractmethod
    async def change_amount(
        self, ctx: TenantContext, budget_id: UUID, amount: Amount, expected_version: int
    ) -> Budget:
        """A new amount, conditioned on the version the caller read
        (`PreconditionFailed` when it moved). A raise is the instruction to
        continue: its commit asks for the org's sessions parked on a budget to
        be woken, and each one's gate runs again."""
        ...

    @abstractmethod
    async def get_spend(self, ctx: TenantContext, budget_id: UUID) -> Tally:
        """What the budget's current window spent and holds; nothing yet is a
        tally of zeros."""
        ...

    @abstractmethod
    async def purge_tenant(self, ctx: TenantContext) -> int:
        """The sweep, for one tenant past its own retention: every budget, a
        batch at most a call. Any other tenant returns 0 and reads nothing."""
        ...

    @abstractmethod
    async def purge_ledger(self, ctx: TenantContext) -> int:
        """The sweep, for one tenant past its own retention. No serving login
        may delete a hold or a settlement (ADR 1006), so it deletes nothing:
        it answers how many rows of the ledger the tenant still keeps, fewer
        than a whole batch, so the sweep never marks the tenant purged while
        its ledger remains and does not call again in the same pass. Any
        other tenant returns 0 and reads nothing."""
        ...
