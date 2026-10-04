import logging
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from acme.integrations.model_providers.calls import ModelCall
from acme.integrations.model_providers.types import Usage
from acme.om.agent_sessions import AgentSessionsManagerInterface
from acme.om.attribution.types.principal import Principal
from acme.om.base import Platform, new_id, utcnow
from acme.om.budgets import BudgetGateInterface, BudgetsManagerInterface
from acme.om.budgets.pricing import ModelPrice, PricingInterface
from acme.om.budgets.rules import call_exposure, job_exposure, usage_spend
from acme.om.budgets.types.amount import Spend
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
from acme.om.budgets.types.usage import CallSite, UsageRecord
from acme.om.context import TenantContext
from acme.om.exceptions import BudgetRefused, Unavailable
from acme.om.models.types.fill import Fill, ModelRole
from acme.om.windows.gate import CallGateInterface
from acme.om.windows.rules import call_shape

log = logging.getLogger(__name__)


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
        self,
        ctx: TenantContext,
        hold_id: UUID,
        usage: Usage | None,
        *,
        billed: bool,
        site: CallSite | None,
    ) -> None:
        raise Unavailable("no budget gate is wired, so there is no hold to settle")

    async def authorize_job(
        self,
        ctx: TenantContext,
        session_id: UUID,
        spender: Principal,
        tool: str,
        rate_micros_per_hour: int,
        deadline: datetime,
    ) -> UUID:
        raise Unavailable("no budget gate is wired, so no job that spends is started")

    async def settle_job(
        self, ctx: TenantContext, hold_id: UUID, cost_micros: int | None, *, started: bool
    ) -> None:
        raise Unavailable("no budget gate is wired, so there is no hold to settle")


class _Held(Platform):
    """What the gate read of a call at its hold and keeps until it settles:
    its price, and what its usage record names."""

    price: ModelPrice | None
    session_id: UUID
    tree_id: UUID
    agent_kind: str
    kind_version: int
    role: ModelRole
    provider: str
    model: str


class CallGateBudgetImpl(CallGateInterface):
    """The budgets' gate behind the narrow face the windows and the loop
    read. A model call's worst case is priced from the one source of prices
    (`windows.rules.call_shape`, `budgets.rules.call_exposure`) and held on
    every scope the call serves: its session, its tree, the person who pays,
    and the tenant. A refusal raises `BudgetRefused`, listing every breach,
    with nothing held. A settlement prices the usage the provider reported
    at the same list price; a call the provider never processed releases
    its hold. A spending job is held on the same scopes at its rate until
    its deadline (`budgets.rules.job_exposure`), and settles at the cost
    its runner reported, else whole; a job refused before any work began
    releases its hold. A model call billed at its reported usage leaves a
    usage record, priced as its settlement is, in every storage mode
    (ADR 1014)."""

    def __init__(
        self,
        gate: BudgetGateInterface,
        pricing: PricingInterface,
        sessions: AgentSessionsManagerInterface,
        budgets: BudgetsManagerInterface,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._gate = gate
        self._pricing = pricing
        self._sessions = sessions
        self._budgets = budgets
        self._clock = clock
        self._held: dict[UUID, _Held] = {}

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
        self._held[answer.id] = _Held(
            price=price,
            session_id=session_id,
            tree_id=session.root_id,
            agent_kind=session.kind,
            kind_version=session.kind_version,
            role=role,
            provider=fill.provider.value,
            model=fill.model,
        )
        return answer.id

    async def settle(
        self,
        ctx: TenantContext,
        hold_id: UUID,
        usage: Usage | None,
        *,
        billed: bool,
        site: CallSite | None,
    ) -> None:
        held = self._held.pop(hold_id, None)
        price = None if held is None else held.price
        bill: Bill
        if not billed:
            bill = NotBilled(proof=NotBilledProof.REFUSED_BEFORE_PROCESSING)
        elif usage is None:
            bill = BillUnknown()
        else:
            bill = Billed(usage=usage_spend(usage, price))
        await self._gate.settle(ctx, hold_id, bill)
        if isinstance(bill, Billed) and usage is not None and site is not None:
            if held is None:
                # This process never held the call, so it has no labels to
                # write: the ledger still counts it.
                log.error("hold %s settled with no usage record: not held here", hold_id)
                return
            await self._budgets.record_usage(
                ctx, _record(hold_id, held, usage, bill.usage.cost_micros, site, self._clock())
            )

    async def authorize_job(
        self,
        ctx: TenantContext,
        session_id: UUID,
        spender: Principal,
        tool: str,
        rate_micros_per_hour: int,
        deadline: datetime,
    ) -> UUID:
        session = await self._sessions.get_session(ctx, session_id)
        request = HoldRequest(
            spender_id=spender.id,
            scopes=scopes_of(ctx.org_id, session_id, session.root_id, spender),
            exposure=job_exposure(rate_micros_per_hour, self._clock(), deadline),
            session_id=session_id,
            purpose=tool,
        )
        answer = await self._gate.authorize(ctx, request)
        if isinstance(answer, Refusal):
            raise BudgetRefused(answer)
        return answer.id

    async def settle_job(
        self, ctx: TenantContext, hold_id: UUID, cost_micros: int | None, *, started: bool
    ) -> None:
        bill: Bill
        if not started:
            bill = NotBilled(proof=NotBilledProof.REFUSED_BEFORE_PROCESSING)
        elif cost_micros is None:
            bill = BillUnknown()
        else:
            bill = Billed(usage=Spend(cost_micros=cost_micros, tokens=0))
        await self._gate.settle(ctx, hold_id, bill)


def _record(
    hold_id: UUID,
    held: _Held,
    usage: Usage,
    cost_micros: int | None,
    site: CallSite,
    at: datetime,
) -> UsageRecord:
    """A billed call's usage record: ids, its tokens by class, its cost at
    the price its hold read, its latency, and labels; no content."""
    return UsageRecord(
        id=new_id(),
        created_at=at,
        hold_id=hold_id,
        session_id=held.session_id,
        tree_id=held.tree_id,
        loop_id=site.loop_id,
        step_id=site.step_id,
        agent_kind=held.agent_kind,
        kind_version=held.kind_version,
        role=held.role,
        provider=held.provider,
        model=held.model,
        input_tokens=usage.input,
        cache_read_tokens=usage.cache_read,
        cache_write_tokens=usage.cache_write,
        output_tokens=usage.output,
        thinking_tokens=usage.thinking,
        cost_micros=cost_micros,
        latency_ms=site.latency_ms,
    )


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
