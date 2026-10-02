from uuid import UUID

from acme.integrations.model_providers.calls import ModelCall
from acme.integrations.model_providers.types import Usage
from acme.om.agent_sessions import AgentSessionsManagerInterface
from acme.om.attribution.types.principal import Principal
from acme.om.budgets import BudgetGateInterface
from acme.om.budgets.pricing import ModelPrice, PricingInterface
from acme.om.budgets.rules import call_exposure, usage_spend
from acme.om.budgets.types.breach import Refusal
from acme.om.budgets.types.budget import BudgetScope, BudgetScopeKind
from acme.om.budgets.types.hold import (
    Bill,
    Billed,
    BillUnknown,
    HoldRequest,
    NotBilled,
    NotBilledProof,
)
from acme.om.context import TenantContext
from acme.om.exceptions import BudgetRefused, Unavailable
from acme.om.models.types.fill import Fill, ModelRole
from acme.om.windows.gate import CallGateInterface
from acme.om.windows.rules import call_shape


class CallGateNullImpl(CallGateInterface):
    """The gate of a root that wired none. It is loud: a compaction spends,
    and a call that skipped the gate would spend outside every budget, so it
    refuses and says why."""

    async def authorize(
        self,
        ctx: TenantContext,
        session_id: UUID,
        spender: Principal,
        role: ModelRole,
        fill: Fill,
        call: ModelCall,
    ) -> UUID:
        raise Unavailable("no budget gate is wired, so no compaction is called")

    async def settle(
        self, ctx: TenantContext, hold_id: UUID, usage: Usage | None, *, billed: bool
    ) -> None:
        raise Unavailable("no budget gate is wired, so there is no hold to settle")


class CallGateBudgetImpl(CallGateInterface):
    """The budgets' gate behind the narrow face the windows and the loop
    read. A model call's worst case is priced from the one source of prices
    (`windows.rules.call_shape`, `budgets.rules.call_exposure`) and held on
    every scope the call serves: its session, its tree, the person who pays,
    and the tenant. A refusal raises `BudgetRefused`, listing every breach,
    with nothing held. A settlement prices the usage the provider reported
    at the same list price; a call the provider never processed releases
    its hold."""

    def __init__(
        self,
        gate: BudgetGateInterface,
        pricing: PricingInterface,
        sessions: AgentSessionsManagerInterface,
    ) -> None:
        self._gate = gate
        self._pricing = pricing
        self._sessions = sessions
        self._prices: dict[UUID, ModelPrice | None] = {}

    async def authorize(
        self,
        ctx: TenantContext,
        session_id: UUID,
        spender: Principal,
        role: ModelRole,
        fill: Fill,
        call: ModelCall,
    ) -> UUID:
        session = await self._sessions.get_session(ctx, session_id)
        price = self._pricing.price_of(fill.provider.value, fill.model)
        request = HoldRequest(
            spender_id=spender.id,
            scopes=scopes_of(ctx.org_id, session_id, session.root_id, spender),
            exposure=call_exposure(call_shape(call, fill), price),
            session_id=session_id,
            purpose=role,
        )
        answer = await self._gate.authorize(ctx, request)
        if isinstance(answer, Refusal):
            raise BudgetRefused(answer)
        self._prices[answer.id] = price
        return answer.id

    async def settle(
        self, ctx: TenantContext, hold_id: UUID, usage: Usage | None, *, billed: bool
    ) -> None:
        price = self._prices.pop(hold_id, None)
        bill: Bill
        if not billed:
            bill = NotBilled(proof=NotBilledProof.REFUSED_BEFORE_PROCESSING)
        elif usage is None:
            bill = BillUnknown()
        else:
            bill = Billed(usage=usage_spend(usage, price))
        await self._gate.settle(ctx, hold_id, bill)


def scopes_of(
    org_id: UUID, session_id: UUID, tree_id: UUID, spender: Principal
) -> tuple[BudgetScope, ...]:
    """The scopes a model call of a session is charged to: the session, the
    tree it draws on, the person who pays, and the tenant. A project's or a
    team's are the platform's to add."""
    return (
        BudgetScope(kind=BudgetScopeKind.SESSION, key=str(session_id)),
        BudgetScope(kind=BudgetScopeKind.TREE, key=str(tree_id)),
        BudgetScope(kind=BudgetScopeKind.PERSON, key=str(spender.id)),
        BudgetScope(kind=BudgetScopeKind.TENANT, key=str(org_id)),
    )
