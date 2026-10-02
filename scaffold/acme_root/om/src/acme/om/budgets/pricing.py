"""What the gate reads of a model's price: list rates, in millionths of the
reference currency per million tokens, the long-context tiers that raise
them, and the fee of each tool the provider runs itself.

One source supplies prices, and every model a resolver can pick has a row of
its own: a model priced by a default row turns every figure built on it,
the budgets that bind on it included, into a guess. `PriceSource` is the
narrow seam the engine reads a row through."""

from typing import Protocol, Self

from pydantic import Field, model_validator

from acme.om.base import FrozenMapping, Platform


class Rates(Platform):
    """List rates per million tokens. Thinking is billed at the output rate
    unless the model names one of its own."""

    input: int = Field(ge=0)
    cache_write: int = Field(ge=0)
    cache_read: int = Field(ge=0)
    output: int = Field(ge=0)
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


class PriceSource(Protocol):
    def price_of(self, provider: str, model: str) -> ModelPrice | None:
        """The model's row, or None when the source has none. None is never
        a default row's price: the gate refuses a call whose cost it cannot
        bound on every line that bounds cost."""
        ...
