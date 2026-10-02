"""The budget gate: one check before every model call and every job that
spends, compaction and side model roles included.

A check after the call overshoots every stop by one call and lets two
sessions slip under one line in the same second. So the gate holds the
call's worst case on every line before the call, and a settlement closes
the hold after it. The engine fails closed for spend: when it cannot tell
who pays, nothing is spent."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.budgets.types.breach import Refusal
from acme.om.budgets.types.hold import Bill, Hold, HoldRequest, Settlement
from acme.om.context import TenantContext


class BudgetGateInterface(ABC):
    @abstractmethod
    async def authorize(self, ctx: TenantContext, request: HoldRequest) -> Hold | Refusal:
        """A hold of the request's worst case on every budget of its scopes
        and its own amount, or a refusal listing every breach, each with the
        action that clears it and when its window resets; nothing is held
        then. A request that names no spender is `SpenderUnknown`, and more
        budgets binding it than the gate reads is `ValidationFailed`: in both
        nothing is spent."""
        ...

    @abstractmethod
    async def settle(self, ctx: TenantContext, hold_id: UUID, bill: Bill) -> Settlement:
        """Closes a hold once: released only when the provider provably did
        not bill, otherwise counted at the usage, else at the whole hold
        (`budgets.rules.spent_by`). A spend past the hold is recorded and
        alarmed, never absorbed. A hold closed already answers its first
        settlement; one the tenant does not hold is `NotFound`."""
        ...
