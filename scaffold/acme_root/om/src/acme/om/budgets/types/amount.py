"""What a budget counts: reference cost and native tokens.

Reference cost is what a call's usage costs at list price, whoever paid, in
millionths of the reference currency, so every figure is a whole number and
two figures compare exactly. Native tokens are the provider's own count. A
budget binds on either or both; a token budget still binds when no price is
known or a model is free."""

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from acme.om.base import Platform


class AmountUnit(StrEnum):
    COST = "cost"  # reference cost, in millionths
    TOKENS = "tokens"  # native tokens


class Amount(Platform):
    """A budget's amount, or a request's own: a unit left None is not
    bounded, and an amount bounds at least one."""

    cost_micros: int | None = Field(default=None, ge=0)
    tokens: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _bounds_something(self) -> Self:
        if self.cost_micros is None and self.tokens is None:
            raise ValueError("an amount bounds reference cost, native tokens, or both")
        return self

    def of(self, unit: AmountUnit) -> int | None:
        return self.cost_micros if unit is AmountUnit.COST else self.tokens


class Spend(Platform):
    """What a call or a job spends, or may spend: its reference cost and its
    native tokens. A cost of None is unknown, because no price applies."""

    cost_micros: int | None = Field(default=None, ge=0)
    tokens: int = Field(default=0, ge=0)

    def of(self, unit: AmountUnit) -> int | None:
        return self.cost_micros if unit is AmountUnit.COST else self.tokens


NOTHING = Spend(cost_micros=0, tokens=0)
"""What a call the provider provably did not bill spends."""
