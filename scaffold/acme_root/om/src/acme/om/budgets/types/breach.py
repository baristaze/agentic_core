"""A refusal and its breaches.

A refusal lists every breach, never only the first, because clearing one
would reveal the next. Each breach names the one action that clears it and
when its window resets."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from acme.om.base import Platform
from acme.om.budgets.types.amount import AmountUnit
from acme.om.budgets.types.budget import BudgetScope


class BreachAction(StrEnum):
    RAISE = "raise"  # raise the amount to at least `needed`
    PRICE = "price"  # give the model a price: the call's cost is unknown and the line bounds cost


class Breach(Platform):
    """One unit of one line the call does not fit under. A line with no
    budget is the request's own amount."""

    budget_id: UUID | None  # None for the request's own amount
    scope: BudgetScope | None
    unit: AmountUnit
    amount: int  # the line's amount in this unit
    committed: int  # what the window spent and holds already
    held: int  # of that, what calls not yet settled hold
    exposure: int | None  # the call's worst case in this unit; None when its cost is unknown
    action: BreachAction
    needed: int | None  # the amount that clears it, for a raise
    resets_at: datetime | None  # None for a window that never resets, and the request's own


class Refusal(Platform):
    breaches: tuple[Breach, ...] = Field(min_length=1)
