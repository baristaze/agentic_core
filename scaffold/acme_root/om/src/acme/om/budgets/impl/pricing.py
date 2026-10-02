"""The one source of prices: the list table, each row read from its
provider's published list on the date it names. A model a resolver can pick
gets its row here in the same change, or it has no price."""

from datetime import date

from acme.om.budgets.pricing import (
    ModelPrice,
    PriceRow,
    PriceTable,
    PriceTier,
    PricingInterface,
    Rates,
)

READ = date(2026, 10, 2)

WEB_SEARCH = {"web_search": 10_000}
"""Both providers bill a search their own tool runs at 10 per 1,000."""


def _usd(per_million: str) -> int:
    """A list rate in dollars per million tokens, in millionths."""
    dollars, _, cents = per_million.partition(".")
    return int(dollars) * 1_000_000 + int((cents + "000000")[:6])


def _anthropic(
    model: str, inputs: str, five_minute: str, hour: str, read: str, out: str
) -> PriceRow:
    """A row of Anthropic's list: the five-minute cache's write is the short
    one, the hour's the long one; thinking is billed as output. A model of
    the 4.6 generation or later bills its whole context at one rate."""
    rates = Rates(
        input=_usd(inputs),
        cache_write=_usd(five_minute),
        cache_write_long=_usd(hour),
        cache_read=_usd(read),
        output=_usd(out),
    )
    price = ModelPrice(rates=rates, tool_fees=WEB_SEARCH)
    return PriceRow(provider="anthropic", model=model, price=price, as_of=READ)


def _openai(
    model: str, short: tuple[str, str, str, str], long: tuple[str, str, str, str]
) -> PriceRow:
    """A row of OpenAI's list: input, cached input, cache write, and output,
    for a prompt of 272K tokens or less and for a longer one, whose rates
    apply to the whole call, output included. Thinking is billed as output."""

    def rates(row: tuple[str, str, str, str]) -> Rates:
        inputs, read, write, out = row
        return Rates(
            input=_usd(inputs), cache_read=_usd(read), cache_write=_usd(write), output=_usd(out)
        )

    price = ModelPrice(
        rates=rates(short),
        tiers=(PriceTier(above=272_000, rates=rates(long)),),
        tool_fees=WEB_SEARCH,
    )
    return PriceRow(provider="openai", model=model, price=price, as_of=READ)


LIST_PRICES = PriceTable(
    version="2026-10-02",
    rows=(
        _anthropic("claude-opus-5-5", "4", "5", "8", "0.20", "20"),
        _anthropic("claude-sonnet-5-5", "2", "2.50", "4", "0.20", "10"),
        _anthropic("claude-haiku-4-5", "1", "1.25", "2", "0.10", "5"),
        _anthropic("claude-haiku-4-5-20251001", "1", "1.25", "2", "0.10", "5"),
        _openai("gpt-6-astra", ("10", "1", "12.50", "50"), ("20", "2", "25", "75")),
        _openai("gpt-6.1-sol", ("2", "0.10", "2.50", "10"), ("4", "0.20", "5", "15")),
        _openai("gpt-6-luna", ("0.10", "0.01", "0.125", "0.50"), ("0.20", "0.02", "0.25", "0.75")),
    ),
)
"""Anthropic's and OpenAI's list prices, read on 2 October 2026, for every
model the engine's adapters name: the pinned Haiku snapshot beside its
alias."""


class PricingTableImpl(PricingInterface):
    def __init__(self, table: PriceTable = LIST_PRICES) -> None:
        self._rows = {(row.provider, row.model): row.price for row in table.rows}

    def price_of(self, provider: str, model: str) -> ModelPrice | None:
        return self._rows.get((provider, model))
