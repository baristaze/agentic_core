"""Pricing: what a model's usage costs at list price, from one source. Every
model a resolver can pick has a row of its own; a model priced by a default
row would make every figure built on it a guess, the budgets that bind on
it included. So there is no default row, and a model with none is
unpriced: the resolver refuses it.

A price is the reference cost, whoever pays: a tenant's own key changes
who pays the provider, never what a call costs here."""

from abc import ABC, abstractmethod
from datetime import date
from decimal import Decimal
from typing import Self

from pydantic import Field, model_validator

from acme.integrations.model_providers.types import ProviderName, Usage
from acme.om.base import Platform

PER = Decimal(1_000_000)
"""Rates are per million tokens."""


class Rates(Platform):
    """List prices per million tokens, one per usage class."""

    input: Decimal = Field(ge=0)
    cache_read: Decimal = Field(ge=0)
    cache_write: Decimal = Field(ge=0)
    output: Decimal = Field(ge=0)
    thinking: Decimal = Field(ge=0)

    def cost(self, usage: Usage) -> Decimal:
        return (
            usage.input * self.input
            + usage.cache_read * self.cache_read
            + usage.cache_write * self.cache_write
            + usage.output * self.output
            + usage.thinking * self.thinking
        ) / PER


class Price(Platform):
    """One model's row. A provider that charges more once a prompt passes a
    length names the length and the rates past it, which apply to the whole
    call, output included."""

    provider: ProviderName
    model: str = Field(min_length=1, max_length=200)
    rates: Rates
    long_context_above: int | None = Field(default=None, gt=0)  # prompt tokens
    long_context: Rates | None = None
    as_of: date  # when the row was read from the provider's list

    @model_validator(mode="after")
    def _a_tier_is_whole(self) -> Self:
        if (self.long_context_above is None) != (self.long_context is None):
            raise ValueError("a long-context tier names its length and its rates together")
        return self

    def rates_for(self, prompt_tokens: int) -> Rates:
        """The rates a call with this many prompt tokens pays."""
        if self.long_context is not None and self.long_context_above is not None:
            if prompt_tokens > self.long_context_above:
                return self.long_context
        return self.rates

    @property
    def highest(self) -> Rates:
        """The highest rates that can apply to a call: a gate's worst case."""
        return self.long_context or self.rates

    def cost(self, usage: Usage) -> Decimal:
        """The reference cost of a call's usage."""
        return self.rates_for(usage.prompt).cost(usage)


class PricingInterface(ABC):
    @abstractmethod
    def price(self, provider: ProviderName, model: str) -> Price | None:
        """The model's row, or None when it has none: never a default."""
        ...

    @abstractmethod
    def describe(self) -> str: ...
