"""The one source of prices: a table of list prices, each row read from its
provider's published list on the date it names. A model added to a
resolver's table gets its row here in the same change, or the resolver
refuses it at boot."""

from datetime import date
from decimal import Decimal

from acme.integrations.model_providers.types import ProviderName
from acme.om.budgets.pricing import Price, PricingInterface, Rates

READ = date(2026, 10, 2)


def _rates(input: str, cache_read: str, cache_write: str, output: str) -> Rates:
    """A row's rates. Thinking is billed as output by both providers."""
    return Rates(
        input=Decimal(input),
        cache_read=Decimal(cache_read),
        cache_write=Decimal(cache_write),
        output=Decimal(output),
        thinking=Decimal(output),
    )


LIST_PRICES: tuple[Price, ...] = (
    # A cache write is 1.25 times the input rate, for the five-minute cache.
    Price(
        provider=ProviderName.ANTHROPIC,
        model="claude-opus-5-5",
        rates=_rates("4.00", "0.20", "5.00", "20.00"),
        as_of=READ,
    ),
    Price(
        provider=ProviderName.ANTHROPIC,
        model="claude-sonnet-5-5",
        rates=_rates("2.00", "0.20", "2.50", "10.00"),
        as_of=READ,
    ),
    Price(
        provider=ProviderName.ANTHROPIC,
        model="claude-haiku-4-5",
        rates=_rates("1.00", "0.10", "1.25", "5.00"),
        as_of=READ,
    ),
    # The provider writes its cache unasked and bills no write, so a write
    # is priced as input. Past 272K prompt tokens a call pays the long rates.
    Price(
        provider=ProviderName.OPENAI,
        model="gpt-6-astra",
        rates=_rates("10.00", "1.00", "10.00", "50.00"),
        long_context_above=272_000,
        long_context=_rates("20.00", "2.00", "20.00", "75.00"),
        as_of=READ,
    ),
    Price(
        provider=ProviderName.OPENAI,
        model="gpt-6.1-sol",
        rates=_rates("2.00", "0.10", "2.00", "10.00"),
        long_context_above=272_000,
        long_context=_rates("4.00", "0.20", "4.00", "15.00"),
        as_of=READ,
    ),
    Price(
        provider=ProviderName.OPENAI,
        model="gpt-6-luna",
        rates=_rates("0.10", "0.01", "0.10", "0.50"),
        long_context_above=272_000,
        long_context=_rates("0.20", "0.02", "0.20", "0.75"),
        as_of=READ,
    ),
)


class PricingTableImpl(PricingInterface):
    def __init__(self, prices: tuple[Price, ...] = LIST_PRICES) -> None:
        rows: dict[tuple[ProviderName, str], Price] = {}
        for price in prices:
            key = (price.provider, price.model)
            if key in rows:
                raise ValueError(f"{price.provider.value} {price.model} has two rows")
            rows[key] = price
        self._rows = rows

    def price(self, provider: ProviderName, model: str) -> Price | None:
        return self._rows.get((provider, model))

    def describe(self) -> str:
        return f"pricing: the list table, {len(self._rows)} models"
