import logging
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from acme.infra.base import QuietNull
from acme.infra.observability import OUTCOMES
from acme.om.base import OmBase, new_id, utcnow
from acme.om.context import Permission, TenantContext
from acme.om.exceptions import NotFound, SpenderUnknown, ValidationFailed
from acme.om.spend_budgets.gate import SpendBudgetGateInterface
from acme.om.spend_budgets.rules import lines_of, spend_settlement_of
from acme.om.spend_budgets.storage import LedgerStorageInterface, SpendBudgetStorageInterface
from acme.om.spend_budgets.types.amount import NOTHING
from acme.om.spend_budgets.types.spend_breach import Refusal
from acme.om.spend_budgets.types.spend_hold import (
    Bill,
    Billed,
    SpendHold,
    SpendHoldRequest,
    SpendSettlement,
)

log = logging.getLogger(__name__)


class SpendBudgetGateOptions(OmBase):
    max_lines: int = 32  # budgets one call may be charged to, at most


class SpendBudgetGateImpl(SpendBudgetGateInterface):
    def __init__(
        self,
        budgets: SpendBudgetStorageInterface,
        ledger: LedgerStorageInterface,
        options: SpendBudgetGateOptions,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._budgets = budgets
        self._ledger = ledger
        self._options = options
        self._clock = clock

    async def authorize(self, ctx: TenantContext, request: SpendHoldRequest) -> SpendHold | Refusal:
        ctx.require(Permission.WRITE)
        if request.spender_id is None:
            raise SpenderUnknown("the engine cannot tell who pays for this call; nothing is spent")
        bound = self._options.max_lines
        found = await self._budgets.read_spend_budgets_for(ctx.org_id, request.scopes, bound + 1)
        if len(found) > bound:
            # A line the gate does not read is spend outside it: refuse.
            raise ValidationFailed(f"more than {bound} budgets bind this call")
        now = self._clock()
        hold = SpendHold(
            id=new_id(),
            created_at=now,
            spender_id=request.spender_id,
            agent_session_id=request.agent_session_id,
            purpose=request.purpose,
            exposure=request.exposure,
            own=request.own,
            lines=lines_of(found, now),
        )
        refusal = await self._ledger.open_spend_hold(ctx.org_id, hold)
        if refusal is not None:
            log.info(
                "refused %s in org %s: %d breaches",
                request.purpose,
                ctx.org_id,
                len(refusal.breaches),
            )
            OUTCOMES.labels(subsystem="spend_budgets", outcome="refused").inc()
            return refusal
        OUTCOMES.labels(subsystem="spend_budgets", outcome="held").inc()
        return hold

    async def settle(self, ctx: TenantContext, spend_hold_id: UUID, bill: Bill) -> SpendSettlement:
        ctx.require(Permission.WRITE)
        hold = await self._ledger.read_hold(ctx.org_id, spend_hold_id)
        if hold is None:
            raise NotFound(f"hold {spend_hold_id} not found")
        settlement = spend_settlement_of(hold, bill, new_id(), self._clock())
        stored = await self._ledger.close_spend_hold(ctx.org_id, settlement)
        if stored.id != settlement.id:
            return stored  # closed already: the first settlement counts
        if stored.overshoot is not None:
            # Counted in full, never absorbed: the hold under-covered the call.
            log.error(
                "hold %s in org %s settled past its worst case by %s micros and %d tokens",
                hold.id,
                ctx.org_id,
                stored.overshoot.cost_micros,
                stored.overshoot.tokens,
            )
            OUTCOMES.labels(subsystem="spend_budgets", outcome="overshoot").inc()
        OUTCOMES.labels(subsystem="spend_budgets", outcome=f"settled_{stored.bill.kind}").inc()
        return stored


class SpendBudgetGateNullImpl(SpendBudgetGateInterface, QuietNull):
    """The gate of a root that wired none. It is quiet, and its degraded
    answer is declared: every call passes with a hold of no lines, which
    bounds nothing, and a settlement counts nowhere. It still fails closed
    for an unknown payer. A root refuses it outside `local`, where a call
    would spend outside every budget."""

    def __init__(self, clock: Callable[[], datetime] = utcnow) -> None:
        self._clock = clock

    async def authorize(self, ctx: TenantContext, request: SpendHoldRequest) -> SpendHold | Refusal:
        if request.spender_id is None:
            raise SpenderUnknown("the engine cannot tell who pays for this call; nothing is spent")
        return SpendHold(
            id=new_id(),
            created_at=self._clock(),
            spender_id=request.spender_id,
            agent_session_id=request.agent_session_id,
            purpose=request.purpose,
            exposure=request.exposure,
            own=request.own,
        )

    async def settle(self, ctx: TenantContext, spend_hold_id: UUID, bill: Bill) -> SpendSettlement:
        spent = bill.usage if isinstance(bill, Billed) else NOTHING
        return SpendSettlement(
            id=new_id(),
            created_at=self._clock(),
            spend_hold_id=spend_hold_id,
            bill=bill,
            spent=spent,
        )
