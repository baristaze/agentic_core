"""Pricing: what a model's usage costs at list price, whoever pays, from one
source. Rates are in millionths of the reference currency per million
tokens, so every figure built on them is a whole number.

Every model a resolver can pick has a row of its own. A model priced by a
default row turns every figure built on it, the budgets that bind on it
included, into a guess, so there is no default row: a model with none has
no price. The one source is the versioned list table
(`budgets.impl.pricing`)."""

from abc import ABC, abstractmethod
from datetime import date
from typing import Self

from pydantic import Field, model_validator

from acme.om.base import FrozenMapping, Platform
from acme.om.budgets.types.budget import MAX_KEY


class Rates(Platform):
    """List rates per million tokens. A cache write is the provider's short
    cache; a provider that also keeps a longer-lived cache names its write
    rate. Thinking is billed at the output rate unless the model names one
    of its own."""

    input: int = Field(ge=0)
    cache_write: int = Field(ge=0)
    cache_read: int = Field(ge=0)
    output: int = Field(ge=0)
    cache_write_long: int | None = Field(default=None, ge=0)
    thinking: int | None = Field(default=None, ge=0)


class PriceTier(Platform):
    """Rates that apply once a prompt passes `above` tokens, to its input and
    its output alike."""

    above: int = Field(ge=0)
    rates: Rates


class ModelPrice(Platform):
    rates: Rates
    tiers: tuple[PriceTier, ...] = ()
    tool_fees: FrozenMapping = Field(default_factory=dict, validate_default=True)
    """The fee of one call of each tool the provider runs itself, by name."""

    @model_validator(mode="after")
    def _fees_are_amounts(self) -> Self:
        for name, fee in self.tool_fees.items():
            if not isinstance(fee, int) or isinstance(fee, bool) or fee < 0:
                raise ValueError(f"tool {name} has no fee in millionths")
        return self


class PriceRow(Platform):
    """One model's price, as its provider lists it on the date the row was read."""

    provider: str = Field(min_length=1, max_length=MAX_KEY)
    model: str = Field(min_length=1, max_length=MAX_KEY)
    price: ModelPrice
    as_of: date


class PriceTable(Platform):
    """The list prices of one reading: its version, and a row per model."""

    version: str = Field(min_length=1, max_length=MAX_KEY)
    rows: tuple[PriceRow, ...]

    @model_validator(mode="after")
    def _one_row_a_model(self) -> Self:
        keys = [(row.provider, row.model) for row in self.rows]
        if len(set(keys)) != len(keys):
            raise ValueError("a model has one row in a price table")
        return self


class PricingInterface(ABC):
    @abstractmethod
    def price_of(self, provider: str, model: str) -> ModelPrice | None:
        """The model's price, or None when the table has no row for it:
        never a default row's."""
        ...
