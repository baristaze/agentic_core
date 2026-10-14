import logging
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from acme.integrations.model_providers.calls import ModelCall
from acme.integrations.model_providers.types import Usage
from acme.om.agent_sessions import AgentSessionsManagerInterface
from acme.om.attribution.types.principal import Principal
from acme.om.base import Platform, new_id, utcnow
from acme.om.context import TenantContext
from acme.om.exceptions import SpendBudgetRefused, Unavailable
from acme.om.language_models.types.fill import Fill, ModelRole
from acme.om.spend_budgets import SpendBudgetGateInterface, SpendBudgetsManagerInterface
from acme.om.spend_budgets.pricing import ModelPrice, PricingInterface
from acme.om.spend_budgets.rules import call_exposure, job_exposure, usage_spend
from acme.om.spend_budgets.types.amount import Spend
from acme.om.spend_budgets.types.spend_breach import Refusal
from acme.om.spend_budgets.types.spend_budget import SpendBudgetScope, SpendBudgetScopeKind
from acme.om.spend_budgets.types.spend_hold import (
    Bill,
    Billed,
    BillUnknown,
    NotBilled,
    NotBilledProof,
    SpendHoldRequest,
)
from acme.om.spend_budgets.types.usage import CallLabels, CallSite, UsageRecord
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
        agent_session_id: UUID,
        spender: Principal,
        role: ModelRole,
        fill: Fill,
        call: ModelCall,
        *,
        credential: str,
    ) -> UUID:
        raise Unavailable("no budget gate is wired, so no compaction is called")

    async def settle(
        self,
        ctx: TenantContext,
        spend_hold_id: UUID,
        usage: Usage | None,
        *,
        billed: bool,
        site: CallSite | None,
        partial: Usage | None = None,
    ) -> None:
        raise Unavailable("no budget gate is wired, so there is no hold to settle")

    async def authorize_job(
        self,
        ctx: TenantContext,
        agent_session_id: UUID,
        spender: Principal,
        tool: str,
        rate_micros_per_hour: int,
        deadline: datetime,
    ) -> UUID:
        raise Unavailable("no budget gate is wired, so no job that spends is started")

    async def settle_job(
        self, ctx: TenantContext, spend_hold_id: UUID, cost_micros: int | None, *, started: bool
    ) -> None:
        raise Unavailable("no budget gate is wired, so there is no hold to settle")


class _Held(Platform):
    """What the gate read of a call at its hold and keeps until it settles:
    its price, and what its usage record names."""

    price: ModelPrice | None
    labels: CallLabels


class CallGateSpendBudgetImpl(CallGateInterface):
    """The budgets' gate behind the narrow face the windows and the loop
    read. A model call's worst case is priced from the one source of prices
    (`windows.rules.call_shape`, `spend_budgets.rules.call_exposure`) and held on
    every scope the call serves: its session, its tree, the person who pays,
    and the tenant. A refusal raises `SpendBudgetRefused`, listing every breach,
    with nothing held. A settlement prices the usage the provider reported
    at the same list price; a call the provider never processed releases
    its hold. A spending job is held on the same scopes at its rate until
    its deadline (`spend_budgets.rules.job_exposure`), and settles at the cost
    its runner reported, else whole; a job refused before any work began
    releases its hold. A billed model call leaves a usage record, in every
    storage mode: priced as its settlement is, or, settled whole, at its
    hold and marked so (ADR 1014)."""

    def __init__(
        self,
        gate: SpendBudgetGateInterface,
        pricing: PricingInterface,
        sessions: AgentSessionsManagerInterface,
        budgets: SpendBudgetsManagerInterface,
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
        agent_session_id: UUID,
        spender: Principal,
        role: ModelRole,
        fill: Fill,
        call: ModelCall,
        *,
        credential: str,
    ) -> UUID:
        session = await self._sessions.get_agent_session(ctx, agent_session_id)
        price = self._pricing.price_of(fill.provider.value, fill.model)
        request = SpendHoldRequest(
            spender_id=spender.id,
            scopes=scopes_of(ctx.org_id, agent_session_id, session.root_id, spender),
            exposure=call_exposure(call_shape(call, fill), price),
            agent_session_id=agent_session_id,
            purpose=role,
        )
        answer = await self._gate.authorize(ctx, request)
        if isinstance(answer, Refusal):
            raise SpendBudgetRefused(answer)
        self._held[answer.id] = _Held(
            price=price,
            labels=CallLabels(
                agent_session_id=agent_session_id,
                tree_id=session.root_id,
                agent_kind=session.kind,
                kind_version=session.kind_version,
                role=role,
                provider=fill.provider.value,
                model=fill.model,
            ),
        )
        return answer.id

    async def settle(
        self,
        ctx: TenantContext,
        spend_hold_id: UUID,
        usage: Usage | None,
        *,
        billed: bool,
        site: CallSite | None,
        partial: Usage | None = None,
    ) -> None:
        held = self._held.pop(spend_hold_id, None)
        price = None if held is None else held.price
        bill: Bill
        if not billed:
            bill = NotBilled(proof=NotBilledProof.REFUSED_BEFORE_PROCESSING)
        elif usage is None:
            bill = BillUnknown()
        else:
            bill = Billed(usage=usage_spend(usage, price))
        settlement = await self._gate.settle(ctx, spend_hold_id, bill)
        if isinstance(bill, NotBilled) or site is None or settlement.bill.kind != bill.kind:
            # Released, never sent, or closed before by another bill, whose
            # settlement wrote any record that was due.
            return
        labels = held.labels if held is not None else site.labels
        if labels is None:
            # Neither this process's hold nor the caller names the call: the
            # ledger still counts it.
            log.error("hold %s settled with no usage record: not held here", spend_hold_id)
            return
        if isinstance(bill, Billed):
            whole, reported, cost = False, usage, bill.usage.cost_micros
        else:
            whole, reported, cost = True, partial, settlement.spent.cost_micros
        try:
            record = _record(spend_hold_id, labels, reported, cost, whole, site, self._clock())
            await self._budgets.record_usage(ctx, record)
        except Exception:
            # The ledger counts the call already: a record that fails to land
            # costs the reading, never the call or the reply it paid for.
            log.exception("hold %s settled with no usage record: the write failed", spend_hold_id)

    async def authorize_job(
        self,
        ctx: TenantContext,
        agent_session_id: UUID,
        spender: Principal,
        tool: str,
        rate_micros_per_hour: int,
        deadline: datetime,
    ) -> UUID:
        session = await self._sessions.get_agent_session(ctx, agent_session_id)
        request = SpendHoldRequest(
            spender_id=spender.id,
            scopes=scopes_of(ctx.org_id, agent_session_id, session.root_id, spender),
            exposure=job_exposure(rate_micros_per_hour, self._clock(), deadline),
            agent_session_id=agent_session_id,
            purpose=tool,
        )
        answer = await self._gate.authorize(ctx, request)
        if isinstance(answer, Refusal):
            raise SpendBudgetRefused(answer)
        return answer.id

    async def settle_job(
        self, ctx: TenantContext, spend_hold_id: UUID, cost_micros: int | None, *, started: bool
    ) -> None:
        bill: Bill
        if not started:
            bill = NotBilled(proof=NotBilledProof.REFUSED_BEFORE_PROCESSING)
        elif cost_micros is None:
            bill = BillUnknown()
        else:
            bill = Billed(usage=Spend(cost_micros=cost_micros, tokens=0))
        await self._gate.settle(ctx, spend_hold_id, bill)


def _record(
    spend_hold_id: UUID,
    labels: CallLabels,
    usage: Usage | None,
    cost_micros: int | None,
    whole: bool,
    site: CallSite,
    at: datetime,
) -> UsageRecord:
    """A billed call's usage record: ids, its tokens by class, its cost as
    the ledger settled it, its latency, and labels; no content. A call
    settled whole is marked, with the tokens its partial reply reported,
    else none."""
    usage = usage or Usage()
    return UsageRecord(
        id=new_id(),
        created_at=at,
        spend_hold_id=spend_hold_id,
        agent_session_id=labels.agent_session_id,
        tree_id=labels.tree_id,
        loop_id=site.loop_id,
        agent_step_id=site.agent_step_id,
        agent_kind=labels.agent_kind,
        kind_version=labels.kind_version,
        role=labels.role,
        provider=labels.provider,
        model=labels.model,
        input_tokens=usage.input,
        cache_read_tokens=usage.cache_read,
        cache_write_tokens=usage.cache_write,
        output_tokens=usage.output,
        thinking_tokens=usage.thinking,
        cost_micros=cost_micros,
        latency_ms=site.latency_ms,
        settled_whole=whole,
    )


def scopes_of(
    org_id: UUID, agent_session_id: UUID, tree_id: UUID, spender: Principal
) -> tuple[SpendBudgetScope, ...]:
    """The scopes a model call of a session is charged to: the session, the
    tree it draws on, the person who pays, and the tenant. A project's or a
    team's are the platform's to add."""
    return (
        SpendBudgetScope(kind=SpendBudgetScopeKind.AGENT_SESSION, key=str(agent_session_id)),
        SpendBudgetScope(kind=SpendBudgetScopeKind.TREE, key=str(tree_id)),
        SpendBudgetScope(kind=SpendBudgetScopeKind.PERSON, key=str(spender.id)),
        SpendBudgetScope(kind=SpendBudgetScopeKind.TENANT, key=str(org_id)),
    )
