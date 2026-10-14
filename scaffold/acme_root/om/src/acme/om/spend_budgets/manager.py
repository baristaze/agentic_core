"""The budgets swimlane: what may be spent, and the gate every model call and
every spending job passes before it starts.

A budget has a scope, a window, and an amount, and binds every call charged
to its scope. Setting one governs what the org's members may spend, so it
takes the permission that governs members; reading one takes READ. The gate
is `SpendBudgetGateInterface`, beside this one. Every model call a provider
billed leaves a usage record with no content (ADR 1014); the operator plane
reads a session's records and their rollups through
`SpendBudgetsOperatorManagerInterface`."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.context import OperatorContext, TenantContext
from acme.om.spend_budgets.types.amount import Amount
from acme.om.spend_budgets.types.spend_budget import SpendBudget, SpendBudgetPage
from acme.om.spend_budgets.types.spend_hold import SpendTally
from acme.om.spend_budgets.types.usage import AgentSessionUsage, UsageRecord


class SpendBudgetsManagerInterface(ABC):
    @abstractmethod
    async def create_spend_budget(self, ctx: TenantContext, budget: SpendBudget) -> SpendBudget:
        """The create, announced. The version and the provenance are the
        manager's. An id written already answers the budget as stored."""
        ...

    @abstractmethod
    async def cap_session(
        self, ctx: TenantContext, agent_session_id: UUID, spend_budget_id: UUID, amount: Amount
    ) -> SpendBudget:
        """A budget of `amount` on one session over its whole life, created as
        `create_spend_budget` creates one: an id written already answers the budget
        as stored, so asking again never adds a second or moves the first.
        A spawn writes its child's share with it. It takes WRITE, not the
        permission that sets budgets: a budget on a session only narrows
        what it may spend, since each of its calls still passes every budget
        of its tree, its spender, and its tenant."""
        ...

    @abstractmethod
    async def get_spend_budget(self, ctx: TenantContext, spend_budget_id: UUID) -> SpendBudget:
        """A budget of the tenant; one another tenant holds is `NotFound`."""
        ...

    @abstractmethod
    async def get_spend_budgets(
        self, ctx: TenantContext, after: UUID | None, limit: int
    ) -> SpendBudgetPage:
        """One page of the tenant's budgets by id, strictly after `after`;
        `limit` is clamped."""
        ...

    @abstractmethod
    async def change_amount(
        self, ctx: TenantContext, spend_budget_id: UUID, amount: Amount, expected_version: int
    ) -> SpendBudget:
        """A new amount, conditioned on the version the caller read
        (`PreconditionFailed` when it moved). A raise is the instruction to
        continue: its commit asks for the org's sessions parked on a budget to
        be woken, and each one's gate runs again."""
        ...

    @abstractmethod
    async def close_spend_budget(self, ctx: TenantContext, spend_budget_id: UUID) -> SpendBudget:
        """Closes the budget for good: from then on the gate never reads it,
        so it binds no call and counts none it holds afterwards, and what it
        counted stays readable. Made on a session's scope before a run and
        closed when the run ends, it bounds that run alone, counted from its
        creation. It takes the permission that sets budgets, since it lifts a
        cap. A close lands whatever amount is stored, and a closed budget
        answers as stored. Its commit asks for the org's sessions parked on
        it to be woken, and each one's gate runs again."""
        ...

    @abstractmethod
    async def get_spend(self, ctx: TenantContext, spend_budget_id: UUID) -> SpendTally:
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


class SpendBudgetsOperatorManagerInterface(ABC):
    """The operator plane of the ledger: one named org's session, read for
    what its model calls used and cost. Takes `OperatorContext` and nothing
    else."""

    @abstractmethod
    async def get_agent_session_usage(
        self,
        admin: OperatorContext,
        org_id: UUID,
        agent_session_id: UUID,
        after: UUID | None,
        limit: int,
    ) -> AgentSessionUsage:
        """A page of the session's usage records, after the id named, and the
        rollups of all of them, per loop and for the session. A record holds
        no content, so this reads the same for every storage mode, and for a
        session whose history was purged. Requires the read permission.
        NotFound when the org is not there, or holds no record of the
        session: another tenant's session reads as one that never called."""
        ...
