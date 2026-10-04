"""The budgets swimlane: what may be spent, and the gate every model call and
every spending job passes before it starts.

A budget has a scope, a window, and an amount, and binds every call charged
to its scope. Setting one governs what the org's members may spend, so it
takes the permission that governs members; reading one takes READ. The gate
is `BudgetGateInterface`, beside this one. Every model call a provider
billed leaves a usage record with no content (ADR 1014); the operator plane
reads a session's records and their rollups through
`BudgetsOperatorManagerInterface`."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.budgets.types.amount import Amount
from acme.om.budgets.types.budget import Budget, BudgetPage
from acme.om.budgets.types.hold import Tally
from acme.om.budgets.types.usage import SessionUsage, UsageRecord
from acme.om.context import OperatorContext, TenantContext


class BudgetsManagerInterface(ABC):
    @abstractmethod
    async def create_budget(self, ctx: TenantContext, budget: Budget) -> Budget:
        """The create, announced. The version and the provenance are the
        manager's. An id written already answers the budget as stored."""
        ...

    @abstractmethod
    async def cap_session(
        self, ctx: TenantContext, session_id: UUID, budget_id: UUID, amount: Amount
    ) -> Budget:
        """A budget of `amount` on one session over its whole life, created as
        `create_budget` creates one: an id written already answers the budget
        as stored, so asking again never adds a second or moves the first.
        A spawn writes its child's share with it. It takes WRITE, not the
        permission that sets budgets: a budget on a session only narrows
        what it may spend, since each of its calls still passes every budget
        of its tree, its spender, and its tenant."""
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
    async def record_usage(self, ctx: TenantContext, record: UsageRecord) -> bool:
        """Writes what one billed model call used and cost, once per hold:
        False, with nothing written, when its hold has a record already.
        Takes WRITE, as a hold does."""
        ...

    @abstractmethod
    async def purge_tenant(self, ctx: TenantContext) -> int:
        """The sweep, for one tenant past its own retention: every budget, a
        batch at most a call. Any other tenant returns 0 and reads nothing."""
        ...


class BudgetsOperatorManagerInterface(ABC):
    """The operator plane of the ledger: one named org's session, read for
    what its model calls used and cost. Takes `OperatorContext` and nothing
    else."""

    @abstractmethod
    async def get_session_usage(
        self,
        admin: OperatorContext,
        org_id: UUID,
        session_id: UUID,
        after: UUID | None,
        limit: int,
    ) -> SessionUsage:
        """A page of the session's usage records, after the id named, and the
        rollups of all of them, per loop and for the session. A record holds
        no content, so this reads the same for every storage mode, and for a
        session whose history was purged. Requires the read permission.
        NotFound when the org is not there, or holds no record of the
        session: another tenant's session reads as one that never called."""
        ...
