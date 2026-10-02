import logging
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from acme.infra.observability import OUTCOMES
from acme.om.base import Platform, new_id, utcnow
from acme.om.budgets.gate import BudgetGateInterface
from acme.om.budgets.rules import lines_of, settlement_of
from acme.om.budgets.storage import BudgetStorageInterface, LedgerStorageInterface
from acme.om.budgets.types.breach import Refusal
from acme.om.budgets.types.hold import Bill, Hold, HoldRequest, Settlement
from acme.om.context import Permission, TenantContext
from acme.om.exceptions import NotFound, SpenderUnknown, ValidationFailed

log = logging.getLogger(__name__)


class BudgetGateOptions(Platform):
    max_lines: int = 32  # budgets one call may be charged to, at most


class BudgetGateImpl(BudgetGateInterface):
    def __init__(
        self,
        budgets: BudgetStorageInterface,
        ledger: LedgerStorageInterface,
        options: BudgetGateOptions,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._budgets = budgets
        self._ledger = ledger
        self._options = options
        self._clock = clock

    async def authorize(self, ctx: TenantContext, request: HoldRequest) -> Hold | Refusal:
        ctx.require(Permission.WRITE)
        if request.spender_id is None:
            raise SpenderUnknown("the engine cannot tell who pays for this call; nothing is spent")
        bound = self._options.max_lines
        found = await self._budgets.read_budgets_for(ctx.org_id, request.scopes, bound + 1)
        if len(found) > bound:
            # A line the gate does not read is spend outside it: refuse.
            raise ValidationFailed(f"more than {bound} budgets bind this call")
        now = self._clock()
        hold = Hold(
            id=new_id(),
            created_at=now,
            spender_id=request.spender_id,
            session_id=request.session_id,
            purpose=request.purpose,
            exposure=request.exposure,
            own=request.own,
            lines=lines_of(found, now),
        )
        refusal = await self._ledger.open_hold(ctx.org_id, hold)
        if refusal is not None:
            log.info(
                "refused %s in org %s: %d breaches",
                request.purpose,
                ctx.org_id,
                len(refusal.breaches),
            )
            OUTCOMES.labels(subsystem="budgets", outcome="refused").inc()
            return refusal
        OUTCOMES.labels(subsystem="budgets", outcome="held").inc()
        return hold

    async def settle(self, ctx: TenantContext, hold_id: UUID, bill: Bill) -> Settlement:
        ctx.require(Permission.WRITE)
        hold = await self._ledger.read_hold(ctx.org_id, hold_id)
        if hold is None:
            raise NotFound(f"hold {hold_id} not found")
        settlement = settlement_of(hold, bill, new_id(), self._clock())
        stored = await self._ledger.close_hold(ctx.org_id, settlement)
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
            OUTCOMES.labels(subsystem="budgets", outcome="overshoot").inc()
        OUTCOMES.labels(subsystem="budgets", outcome=f"settled_{stored.bill.kind}").inc()
        return stored
