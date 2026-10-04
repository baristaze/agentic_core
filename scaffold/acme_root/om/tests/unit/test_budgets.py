"""Budgets on their own and over the memory storage: the window a time falls
in, a call's worst case over a price table, the gate's refusal with every
breach, how a hold settles, and the budgets manager."""

import logging
from datetime import UTC, datetime, timedelta
from itertools import product
from pathlib import Path
from uuid import UUID

import pytest
from contracts.budget_storage import make_budget
from contracts.doubles import context
from contracts.factories import make_org
from pydantic import ValidationError

from acme.infra.impl.local import InfraLocalImpl
from acme.om.base import new_id, utcnow
from acme.om.budgets.impl.gate import BudgetGateImpl, BudgetGateOptions
from acme.om.budgets.impl.pricing import LIST_PRICES, PricingTableImpl
from acme.om.budgets.pricing import ModelPrice, PriceTable, PriceTier, Rates
from acme.om.budgets.rules import (
    EPOCH,
    HOLD_RETRY,
    OWN_AMOUNT_UNLOCK,
    PRICE_UNLOCK,
    budget_park,
    call_exposure,
    job_exposure,
    raises,
    window_bounds,
)
from acme.om.budgets.storage.impl.memory import BudgetStorageMemoryImpl, LedgerStorageMemoryImpl
from acme.om.budgets.types.amount import Amount, AmountUnit, Spend
from acme.om.budgets.types.breach import Breach, BreachAction, Refusal
from acme.om.budgets.types.budget import (
    Budget,
    BudgetScope,
    BudgetScopeKind,
    BudgetWindow,
    WindowKind,
)
from acme.om.budgets.types.exposure import (
    CacheWrite,
    CallShape,
    PromptCount,
    PromptSize,
    ProviderTool,
)
from acme.om.budgets.types.hold import (
    Billed,
    BillUnknown,
    Hold,
    HoldRequest,
    NotBilled,
    NotBilledProof,
)
from acme.om.context import Role, TenantContext
from acme.om.exceptions import (
    NotAuthorized,
    NotFound,
    PreconditionFailed,
    SpenderUnknown,
    ValidationFailed,
)
from acme.om.root import Managers, build_managers
from acme.om.steps.types.header import ParkReason
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.om.work.storage.impl.memory import WorkStorageMemoryImpl
from acme.om.work.types.work_item import WorkItem, WorkKind

# Windows.

AT = datetime(2026, 10, 8, 13, 45, 30, tzinfo=UTC)  # a Thursday


@pytest.mark.parametrize(
    ("window", "start", "resets"),
    [
        (BudgetWindow(kind=WindowKind.LIFE), EPOCH, None),
        (
            BudgetWindow(kind=WindowKind.HOUR),
            AT.replace(minute=0, second=0),
            AT.replace(hour=14, minute=0, second=0),
        ),
        (
            BudgetWindow(kind=WindowKind.DAY),
            datetime(2026, 10, 8, tzinfo=UTC),
            datetime(2026, 10, 9, tzinfo=UTC),
        ),
        (
            BudgetWindow(kind=WindowKind.WEEK),
            datetime(2026, 10, 5, tzinfo=UTC),
            datetime(2026, 10, 12, tzinfo=UTC),
        ),
        (
            BudgetWindow(kind=WindowKind.MONTH),
            datetime(2026, 10, 1, tzinfo=UTC),
            datetime(2026, 11, 1, tzinfo=UTC),
        ),
        (
            BudgetWindow(kind=WindowKind.SPAN, seconds=90),
            AT.replace(second=0),
            AT.replace(minute=46, second=30),
        ),
    ],
)
def test_a_time_falls_in_one_window_which_says_when_it_resets(
    window: BudgetWindow, start: datetime, resets: datetime | None
) -> None:
    assert window_bounds(window, AT) == (start, resets)
    if resets is not None:
        # The reset is the next window's start.
        following, after = window_bounds(window, resets)
        assert following == resets and after is not None and after > resets


def test_december_resets_into_january_and_a_span_only_has_a_length() -> None:
    december = datetime(2026, 12, 31, 23, 59, tzinfo=UTC)
    assert window_bounds(BudgetWindow(kind=WindowKind.MONTH), december)[1] == datetime(
        2027, 1, 1, tzinfo=UTC
    )
    with pytest.raises(ValidationError):
        BudgetWindow(kind=WindowKind.SPAN)
    with pytest.raises(ValidationError):
        BudgetWindow(kind=WindowKind.DAY, seconds=60)
    with pytest.raises(ValidationError):
        make_budget(cost_micros=None, tokens=None)


# The worst case, over a price table.

MILLION = 1_000_000
BASE = Rates(
    input=3 * MILLION,
    cache_write=3_750_000,
    cache_write_long=6 * MILLION,
    cache_read=300_000,
    output=15 * MILLION,
)
LONG = Rates(input=6 * MILLION, cache_write=7_500_000, cache_read=600_000, output=22_500_000)

PRICES: dict[str, ModelPrice] = {
    "plain": ModelPrice(rates=BASE),
    "long": ModelPrice(rates=BASE, tiers=(PriceTier(above=200_000, rates=LONG),)),
    "thinker": ModelPrice(
        rates=Rates(
            input=1_250_000,
            cache_write=1_250_000,
            cache_read=125_000,
            output=10 * MILLION,
            thinking=12 * MILLION,
        )
    ),
    "searcher": ModelPrice(rates=BASE, tool_fees={"web_search": 10_000}),
    "free": ModelPrice(rates=Rates(input=0, cache_write=0, cache_read=0, output=0)),
}
"""Rates in millionths per million tokens: `plain` is 3 in, 3.75 to write the
short cache and 6 the long one, 15 out; past 200k prompt tokens `long`
doubles its input and raises its output by half, and names no long cache;
`thinker` bills thinking at a rate of its own."""


def shape(
    prompt: int,
    output: int,
    *,
    cache: CacheWrite = CacheWrite.NONE,
    thinking: int = 0,
    tools: dict[str, tuple[int, int]] | None = None,
) -> CallShape:
    """A call's shape; `tools` names each provider tool's most calls and the
    most input one call may add."""
    return CallShape(
        prompt=PromptSize(tokens=prompt, counted_by=PromptCount.PROVIDER),
        cache=cache,
        output_bound=output,
        thinking_outside=thinking,
        provider_tools=tuple(
            ProviderTool(name=name, calls=calls, input_per_call=each)
            for name, (calls, each) in (tools or {}).items()
        ),
    )


@pytest.mark.parametrize(
    ("model", "call", "cost"),
    [
        # 10k in at 3, 1k out at 15.
        ("plain", shape(10_000, 1_000), 30_000 + 15_000),
        # A request that writes a cache: every prompt token at that cache's write.
        ("plain", shape(10_000, 1_000, cache=CacheWrite.SHORT), 37_500 + 15_000),
        ("plain", shape(10_000, 1_000, cache=CacheWrite.LONG), 60_000 + 15_000),
        # Below the tier, the base rates; past it, the tier's, the output's too.
        ("long", shape(150_000, 4_000), 450_000 + 60_000),
        ("long", shape(250_000, 4_000), 1_500_000 + 90_000),
        ("long", shape(250_000, 4_000, cache=CacheWrite.SHORT), 1_875_000 + 90_000),
        # Thinking at its own rate, inside the output bound and outside it.
        ("thinker", shape(1_000, 2_000, thinking=8_000), 1_250 + 24_000 + 96_000),
        # The provider's own tool: its fee times the most calls it may make.
        ("searcher", shape(10_000, 1_000, tools={"web_search": (5, 0)}), 45_000 + 50_000),
        # And the input its calls may add, at the input rate: 10k + 5 x 2k.
        ("searcher", shape(10_000, 1_000, tools={"web_search": (5, 2_000)}), 125_000),
        ("free", shape(10_000, 1_000), 0),
    ],
)
def test_the_hold_covers_the_worst_case(model: str, call: CallShape, cost: int) -> None:
    exposure = call_exposure(call, PRICES[model])
    assert exposure.cost_micros == cost
    assert exposure.tokens == call.input_bound + call.output_bound + call.thinking_outside


def billed_at(
    price: ModelPrice,
    cache: CacheWrite,
    prompt: tuple[int, int, int],
    output: int,
    thinking: int,
    searches: int = 0,
) -> int:
    """What a usage costs at the rates that apply to it: the input split into
    plain, cache read, and cache write tokens, the writes at the rate of the
    cache the request wrote, the tier chosen on the whole input, and the fee
    of each search the provider ran."""
    plain, read, written = prompt
    tiers = [t for t in price.tiers if sum(prompt) > t.above]
    rates = tiers[-1].rates if tiers else price.rates
    write = rates.cache_write_long if cache is CacheWrite.LONG else rates.cache_write
    per_token = (
        plain * rates.input
        + read * rates.cache_read
        + written * (write or 0)
        + output * rates.output
        + thinking * (rates.output if rates.thinking is None else rates.thinking)
    )
    fees = searches * int(price.tool_fees.get("web_search", 0))
    return -(-per_token // MILLION) + fees


@pytest.mark.parametrize(
    ("provider", "model"),
    [("table", "plain"), ("table", "long"), ("table", "thinker")]
    + [(row.provider, row.model) for row in LIST_PRICES.rows],
)
def test_no_usage_the_shape_allows_costs_more_than_its_hold(provider: str, model: str) -> None:
    """Whatever mix of plain, cached, and written input tokens the provider
    reports, whatever output and thinking up to their bounds, and, where the
    model's provider runs a search, up to three searches each adding up to
    5k input tokens, which can carry the call past a tier, the usage costs no
    more than the hold: over the rows above and every row of the list table."""
    price = PRICES[model] if provider == "table" else PricingTableImpl().price_of(provider, model)
    assert price is not None
    calls, each = (3, 5_000) if "web_search" in price.tool_fees else (0, 0)
    tools = {"web_search": (calls, each)} if calls else None
    sizes = (1_000, 199_999, 200_001, 260_000, 268_000, 300_000)
    for size, cache in product(sizes, CacheWrite):
        call = shape(size, 4_000, cache=cache, thinking=2_000, tools=tools)
        hold = call_exposure(call, price).cost_micros
        if hold is None:
            continue  # no rate for that cache: the gate refuses it where cost binds
        # The searches run: none, some with part of their input, or all with all of it.
        runs = {(0, 0), (1, each // 2), (calls, each), (calls, calls * each)} if calls else {(0, 0)}
        # The output bound written as text or as thinking, and thinking beyond it.
        turns = [(0, 0), (4_000, 0), (4_000, 2_000), (0, 6_000)]
        for (searches, added), (output, thinking) in product(runs, turns):
            total = size + added
            splits = [(total, 0, 0), (0, total, 0)]
            if cache is not CacheWrite.NONE:
                splits += [(0, 0, total), (total // 2, 0, total - total // 2)]
            for prompt in splits:
                billed = billed_at(price, cache, prompt, output, thinking, searches)
                assert billed <= hold, (model, size, cache, prompt, searches)


ADAPTER_MODELS = [
    ("anthropic", "claude-opus-5-5"),
    ("anthropic", "claude-sonnet-5-5"),
    ("anthropic", "claude-haiku-4-5"),
    ("anthropic", "claude-haiku-4-5-20251001"),
    ("openai", "gpt-6-astra"),
    ("openai", "gpt-6.1-sol"),
    ("openai", "gpt-6-luna"),
]


def test_the_list_table_prices_every_model_the_adapters_name_and_no_other() -> None:
    pricing = PricingTableImpl()
    assert {(row.provider, row.model) for row in LIST_PRICES.rows} == set(ADAPTER_MODELS)
    assert LIST_PRICES.version == "2026-10-02"
    for provider, model in ADAPTER_MODELS:
        assert pricing.price_of(provider, model) is not None, model
    # No default row: an unlisted model, or a listed one under another provider, has no price.
    assert pricing.price_of("anthropic", "claude-unlisted") is None
    assert pricing.price_of("openai", "claude-opus-5-5") is None
    twice = LIST_PRICES.rows[0]
    with pytest.raises(ValidationError, match="one row"):
        PriceTable(version="x", rows=(twice, twice))


@pytest.mark.parametrize(
    ("provider", "model", "call", "cost"),
    [
        # 100k prompt written to the hour's cache at 8, 8k out at 20.
        ("anthropic", "claude-opus-5-5", shape(100_000, 8_000, cache=CacheWrite.LONG), 960_000),
        ("anthropic", "claude-haiku-4-5", shape(10_000, 1_000, cache=CacheWrite.SHORT), 17_500),
        # Past 272k prompt tokens: the long rates, a cache write at 5, 10k out at 15.
        ("openai", "gpt-6.1-sol", shape(300_000, 10_000, cache=CacheWrite.SHORT), 1_650_000),
        # Below the threshold, the short rates, and three searches at 10 per 1,000.
        (
            "openai",
            "gpt-6-astra",
            shape(270_000, 1_000, tools={"web_search": (3, 0)}),
            2_700_000 + 50_000 + 30_000,
        ),
        # Three searches that may add 10k each carry it past: the long rates.
        (
            "openai",
            "gpt-6-astra",
            shape(270_000, 1_000, tools={"web_search": (3, 10_000)}),
            6_000_000 + 75_000 + 30_000,
        ),
        # 20k prompt and five searches of up to 10k at 4, 8k out at 20.
        (
            "anthropic",
            "claude-opus-5-5",
            shape(20_000, 8_000, tools={"web_search": (5, 10_000)}),
            280_000 + 160_000 + 50_000,
        ),
        # The provider keeps no longer cache: its write has no rate.
        ("openai", "gpt-6-luna", shape(1_000, 1_000, cache=CacheWrite.LONG), None),
    ],
)
def test_the_hold_over_the_list_table(
    provider: str, model: str, call: CallShape, cost: int | None
) -> None:
    assert call_exposure(call, PricingTableImpl().price_of(provider, model)).cost_micros == cost


def test_with_no_price_the_cost_is_unknown_and_the_tokens_still_count() -> None:
    assert call_exposure(shape(10_000, 1_000), None) == Spend(cost_micros=None, tokens=11_000)
    unpriced_tool = shape(10_000, 1_000, tools={"code_run": (1, 0)})
    assert call_exposure(unpriced_tool, PRICES["searcher"]).cost_micros is None


def test_a_prompt_size_is_the_providers_count_or_a_proven_bound_and_a_tool_has_a_bound() -> None:
    assert PromptSize(tokens=5, counted_by=PromptCount.UPPER_BOUND).tokens == 5
    with pytest.raises(ValidationError):
        PromptSize.model_validate({"tokens": 5, "counted_by": "estimate"})
    unbounded = {"prompt": {"tokens": 10, "counted_by": "provider"}, "output_bound": 10}
    with pytest.raises(ValidationError):
        CallShape.model_validate(
            {**unbounded, "provider_tools": [{"name": "web_search", "calls": 5}]}
        )
    with pytest.raises(ValidationError, match="one bound"):
        CallShape.model_validate(
            {
                **unbounded,
                "provider_tools": [
                    {"name": "web_search", "calls": 1, "input_per_call": 1},
                    {"name": "web_search", "calls": 2, "input_per_call": 1},
                ],
            }
        )


def test_a_jobs_worst_case_is_its_rate_times_its_deadline() -> None:
    now = utcnow()
    # 3.6 an hour is a thousandth a second; 90.5 seconds round up to 91.
    exposure = job_exposure(3_600_000, now, now + timedelta(seconds=90.5))
    assert exposure == Spend(cost_micros=91_000, tokens=0)
    with pytest.raises(ValueError, match="after its deadline"):
        job_exposure(3_600_000, now, now)


# The gate and the budgets manager.


@pytest.fixture
def managers(tmp_path: Path) -> Managers:
    return build_managers(StorageMemoryImpl(), InfraLocalImpl(tmp_path))


def scope(kind: BudgetScopeKind, key: UUID) -> BudgetScope:
    return BudgetScope(kind=kind, key=str(key))


def request(
    ctx: TenantContext,
    *scopes: BudgetScope,
    cost: int | None = 1_000,
    tokens: int = 4_000,
    own: Amount | None = None,
) -> HoldRequest:
    return HoldRequest(
        spender_id=ctx.user_id,
        scopes=scopes,
        exposure=Spend(cost_micros=cost, tokens=tokens),
        own=own,
        purpose="main",
    )


async def made(managers: Managers, ctx: TenantContext, budget: Budget) -> Budget:
    return await managers.budgets.create_budget(ctx, budget)


async def test_a_refusal_lists_every_breach_with_its_action_and_reset(managers: Managers) -> None:
    ctx = context(Role.ADMIN)
    session, person = new_id(), ctx.user_id
    day = await made(
        managers, ctx, make_budget(BudgetScopeKind.SESSION, str(session), cost_micros=500)
    )
    month = await made(
        managers,
        ctx,
        make_budget(
            BudgetScopeKind.PERSON,
            str(person),
            window=WindowKind.MONTH,
            tokens=3_000,
            cost_micros=None,
        ),
    )
    roomy = await made(
        managers, ctx, make_budget(BudgetScopeKind.TENANT, str(ctx.org_id), cost_micros=10**9)
    )
    asked = request(
        ctx,
        scope(BudgetScopeKind.SESSION, session),
        scope(BudgetScopeKind.PERSON, person),
        scope(BudgetScopeKind.TENANT, ctx.org_id),
        own=Amount(cost_micros=800),
    )
    answer = await managers.budget_gate.authorize(ctx, asked)
    assert isinstance(answer, Refusal)
    found = {(b.budget_id, b.unit): b for b in answer.breaches}
    assert set(found) == {
        (day.id, AmountUnit.COST),
        (month.id, AmountUnit.TOKENS),
        (None, AmountUnit.COST),
    }, "every breach, the second and third included"
    now = utcnow()
    assert found[(day.id, AmountUnit.COST)].resets_at == window_bounds(day.window, now)[1]
    assert found[(month.id, AmountUnit.TOKENS)].resets_at == window_bounds(month.window, now)[1]
    assert found[(None, AmountUnit.COST)].resets_at is None
    assert all(b.action is BreachAction.RAISE for b in answer.breaches)
    assert found[(day.id, AmountUnit.COST)].needed == 1_000
    assert found[(month.id, AmountUnit.TOKENS)].needed == 4_000
    # Nothing is held on any line, the roomy one included.
    for budget in (day, month, roomy):
        spend = await managers.budgets.get_spend(ctx, budget.id)
        assert (spend.held_cost_micros, spend.held_tokens) == (0, 0)


async def test_a_call_that_fits_is_held_on_every_line_of_its_scopes(managers: Managers) -> None:
    ctx = context(Role.ADMIN)
    session = new_id()
    mine = await made(managers, ctx, make_budget(BudgetScopeKind.SESSION, str(session)))
    await made(managers, ctx, make_budget(BudgetScopeKind.SESSION, str(new_id())))
    answer = await managers.budget_gate.authorize(
        ctx, request(ctx, scope(BudgetScopeKind.SESSION, session), cost=250)
    )
    assert isinstance(answer, Hold)
    assert [line.budget_id for line in answer.lines] == [mine.id]
    spend = await managers.budgets.get_spend(ctx, mine.id)
    assert (spend.held_cost_micros, spend.held_tokens) == (250, 4_000)


async def test_the_gate_fails_closed_when_it_cannot_tell_who_pays(managers: Managers) -> None:
    ctx = context(Role.MEMBER)
    unpaid = request(ctx).model_copy(update={"spender_id": None})
    with pytest.raises(SpenderUnknown):
        await managers.budget_gate.authorize(ctx, unpaid)


async def test_more_budgets_than_the_gate_reads_are_refused_never_skipped(tmp_path: Path) -> None:
    storage = StorageMemoryImpl()
    gate = BudgetGateImpl(
        storage.get_budget_storage(), storage.get_ledger_storage(), BudgetGateOptions(max_lines=2)
    )
    ctx = context(Role.ADMIN)
    session = new_id()
    for _ in range(3):
        await storage.get_budget_storage().create_budget(
            ctx.org_id, make_budget(BudgetScopeKind.SESSION, str(session), cost_micros=10**9), ()
        )
    with pytest.raises(ValidationFailed, match="more than 2 budgets"):
        await gate.authorize(ctx, request(ctx, scope(BudgetScopeKind.SESSION, session)))


async def test_a_viewer_neither_spends_nor_sets_a_budget(managers: Managers) -> None:
    org = make_org()
    viewer, member = context(Role.VIEWER, org), context(Role.MEMBER, org)
    with pytest.raises(NotAuthorized):
        await managers.budget_gate.authorize(viewer, request(viewer))
    with pytest.raises(NotAuthorized):
        await managers.budgets.create_budget(member, make_budget())


async def test_a_member_caps_a_session_and_the_cap_never_moves(managers: Managers) -> None:
    """A cap only narrows what one session may spend, so writing one takes
    the permission to write, which a viewer lacks. Asked again under its id,
    with any amount, it answers the cap as written."""
    org = make_org()
    viewer, member = context(Role.VIEWER, org), context(Role.MEMBER, org)
    session, cap_id, amount = new_id(), new_id(), Amount(cost_micros=1_000)
    with pytest.raises(NotAuthorized):
        await managers.budgets.cap_session(viewer, session, cap_id, amount)
    cap = await managers.budgets.cap_session(member, session, cap_id, amount)
    assert (cap.id, cap.scope, cap.window_kind, cap.amount) == (
        cap_id,
        scope(BudgetScopeKind.SESSION, session),
        WindowKind.LIFE,
        amount,
    )
    raised = Amount(cost_micros=9_000)
    assert await managers.budgets.cap_session(member, session, cap_id, raised) == cap


# Settlement.


async def held(managers: Managers, ctx: TenantContext, cost: int = 1_000) -> tuple[Budget, Hold]:
    session = new_id()
    budget = await made(
        managers, ctx, make_budget(BudgetScopeKind.SESSION, str(session), cost_micros=10_000)
    )
    hold = await managers.budget_gate.authorize(
        ctx, request(ctx, scope(BudgetScopeKind.SESSION, session), cost=cost)
    )
    assert isinstance(hold, Hold)
    return budget, hold


@pytest.mark.parametrize(
    ("bill", "spent"),
    [
        # Released only when the provider provably did not bill.
        (NotBilled(proof=NotBilledProof.REFUSED_BEFORE_PROCESSING), Spend(cost_micros=0, tokens=0)),
        (NotBilled(proof=NotBilledProof.NEVER_SENT), Spend(cost_micros=0, tokens=0)),
        # Otherwise at the usage, reported or retrieved later...
        (Billed(usage=Spend(cost_micros=420, tokens=1_500)), Spend(cost_micros=420, tokens=1_500)),
        # ...its cost the hold's when no price gave one...
        (
            Billed(usage=Spend(cost_micros=None, tokens=1_500)),
            Spend(cost_micros=1_000, tokens=1_500),
        ),
        # ...else at the whole hold: a broken stream or a crash after the send.
        (BillUnknown(), Spend(cost_micros=1_000, tokens=4_000)),
    ],
)
async def test_a_hold_is_released_only_when_the_provider_provably_did_not_bill(
    managers: Managers, bill: NotBilled | Billed | BillUnknown, spent: Spend
) -> None:
    ctx = context(Role.ADMIN)
    budget, hold = await held(managers, ctx)
    settlement = await managers.budget_gate.settle(ctx, hold.id, bill)
    assert (settlement.spent, settlement.overshoot) == (spent, None)
    tally = await managers.budgets.get_spend(ctx, budget.id)
    assert (tally.held_cost_micros, tally.held_tokens) == (0, 0)
    assert (tally.spent_cost_micros, tally.spent_tokens) == (spent.cost_micros, spent.tokens)


def test_no_release_comes_without_a_proof() -> None:
    with pytest.raises(ValidationError):
        NotBilled.model_validate({"kind": "not_billed"})
    with pytest.raises(ValidationError):
        NotBilled.model_validate({"kind": "not_billed", "proof": "stream_broke"})


async def test_a_hold_settles_once_and_a_spend_past_it_is_counted_and_alarmed(
    managers: Managers, caplog: pytest.LogCaptureFixture
) -> None:
    ctx = context(Role.ADMIN)
    budget, hold = await held(managers, ctx)
    past = Billed(usage=Spend(cost_micros=1_300, tokens=4_100))
    with caplog.at_level(logging.ERROR, logger="acme.om.budgets.impl.gate"):
        first = await managers.budget_gate.settle(ctx, hold.id, past)
    assert first.overshoot == Spend(cost_micros=300, tokens=100)
    assert "settled past its worst case" in caplog.text
    tally = await managers.budgets.get_spend(ctx, budget.id)
    assert (tally.spent_cost_micros, tally.spent_tokens) == (1_300, 4_100), "never absorbed"
    again = await managers.budget_gate.settle(ctx, hold.id, BillUnknown())
    assert again == first
    assert await managers.budgets.get_spend(ctx, budget.id) == tally
    with pytest.raises(NotFound):
        await managers.budget_gate.settle(ctx, new_id(), BillUnknown())


# The park a refusal asks for.


def breach(
    budget_id: UUID | None,
    resets_at: datetime | None,
    action: BreachAction = BreachAction.RAISE,
    *,
    amount: int = 1_000,
    spent: int = 900,
    held: int = 0,
    exposure: int = 500,
) -> Breach:
    priced = action is not BreachAction.PRICE
    return Breach(
        budget_id=budget_id,
        scope=None if budget_id is None else BudgetScope(kind=BudgetScopeKind.SESSION, key="s"),
        unit=AmountUnit.COST,
        amount=amount,
        committed=spent + held,
        held=held,
        exposure=exposure if priced else None,
        action=action,
        needed=spent + held + exposure if priced else None,
        resets_at=resets_at,
    )


def test_a_budget_parks_until_its_last_window_resets_or_a_person_raises_it() -> None:
    day, month = new_id(), new_id()
    tomorrow, next_month = AT + timedelta(days=1), AT + timedelta(days=24)
    both = Refusal(breaches=(breach(month, next_month), breach(day, tomorrow)))
    park = budget_park(both, AT)
    assert (park.reason, park.unlock, park.retry_at) == (ParkReason.BUDGET, str(month), next_month)
    life = new_id()
    forever = budget_park(Refusal(breaches=(breach(day, tomorrow), breach(life, None))), AT)
    assert (forever.unlock, forever.retry_at) == (str(life), None)
    own = budget_park(Refusal(breaches=(breach(None, None),)), AT)
    assert (own.unlock, own.retry_at) == (OWN_AMOUNT_UNLOCK, None)
    unpriced = budget_park(Refusal(breaches=(breach(day, tomorrow, BreachAction.PRICE),)), AT)
    assert (unpriced.unlock, unpriced.retry_at) == (PRICE_UNLOCK, None)


def test_a_call_no_window_can_hold_waits_on_a_person() -> None:
    """A worst case past the line's amount is refused by every fresh window
    too: only a raise clears it, so the park sets no time to try again."""
    day = new_id()
    tomorrow = AT + timedelta(days=1)
    alone = breach(day, tomorrow, amount=500, spent=0, exposure=800)
    park = budget_park(Refusal(breaches=(alone,)), AT)
    assert (park.unlock, park.retry_at) == (str(day), None)
    with_reset = Refusal(breaches=(alone, breach(new_id(), tomorrow)))
    assert budget_park(with_reset, AT).retry_at is None


def test_a_call_open_holds_refuse_tries_again_soon_and_never_after_the_reset() -> None:
    """A month's line of 1,000 with 600 held by a call not yet settled
    refuses a call of 500, though what the month spent leaves room: its
    settlement frees the room, so the park tries again soon, never at the
    month's end. A window that resets sooner bounds it."""
    month = new_id()
    next_month = datetime(2026, 11, 1, tzinfo=UTC)
    held = breach(month, next_month, spent=0, held=600, exposure=500)
    assert budget_park(Refusal(breaches=(held,)), AT).retry_at == AT + HOLD_RETRY
    in_a_second = AT + timedelta(seconds=1)
    resetting = breach(month, in_a_second, spent=0, held=600, exposure=500)
    assert budget_park(Refusal(breaches=(resetting,)), AT).retry_at == in_a_second
    # What the month spent leaves no room, whatever the holds: the reset.
    spent = breach(month, next_month, spent=600, held=100, exposure=500)
    assert budget_park(Refusal(breaches=(spent,)), AT).retry_at == next_month


# The budgets manager.


def queued(storage: StorageMemoryImpl) -> list[WorkItem]:
    work = storage.get_work_storage()
    assert isinstance(work, WorkStorageMemoryImpl)
    return [item for _, item in work._items.values()]  # pyright: ignore[reportPrivateUsage]


async def test_a_raise_asks_to_wake_the_sessions_parked_on_a_budget(tmp_path: Path) -> None:
    storage = StorageMemoryImpl()
    managers = build_managers(storage, InfraLocalImpl(tmp_path))
    ctx = context(Role.OWNER)
    budget = await made(managers, ctx, make_budget(cost_micros=1_000, tokens=500))
    lowered = await managers.budgets.change_amount(
        ctx, budget.id, Amount(cost_micros=900, tokens=500), 1
    )
    assert lowered.version == 2 and queued(storage) == []
    raised = await managers.budgets.change_amount(
        ctx, budget.id, Amount(cost_micros=900, tokens=900), 2
    )
    assert (raised.tokens, raised.version) == (900, 3)
    (wake,) = queued(storage)
    assert (wake.kind, wake.target_id, dict(wake.payload)) == (
        WorkKind.WAKE_SESSIONS,
        ctx.org_id,
        {"reason": "budget"},
    )
    with pytest.raises(PreconditionFailed):
        await managers.budgets.change_amount(ctx, budget.id, Amount(cost_micros=1), 2)
    assert (await managers.budgets.get_budget(ctx, budget.id)).version == 3


def test_a_raise_is_more_room_in_some_unit() -> None:
    assert raises(Amount(cost_micros=5), Amount(cost_micros=6))
    assert raises(Amount(cost_micros=5), Amount(tokens=6)), "a unit no longer bounded"
    assert not raises(Amount(cost_micros=5, tokens=5), Amount(cost_micros=5, tokens=4))
    assert not raises(Amount(tokens=5), Amount(cost_micros=1, tokens=5))


async def test_budgets_page_by_id_and_another_tenant_finds_none(managers: Managers) -> None:
    ctx = context(Role.ADMIN)
    made_ = [await made(managers, ctx, make_budget()) for _ in range(3)]
    page = await managers.budgets.get_budgets(ctx, None, 2)
    assert page.items == tuple(made_[:2]) and page.has_more
    assert await managers.budgets.create_budget(ctx, made_[0]) == made_[0]
    other = context(Role.OWNER)
    with pytest.raises(NotFound):
        await managers.budgets.get_budget(other, made_[0].id)
    assert (await managers.budgets.get_budgets(other, None, 10)).items == ()


async def test_the_engines_own_ledger_counts_in_process() -> None:
    """The memory twins wired by hand, as a laptop runs the engine."""
    budgets, ledger = BudgetStorageMemoryImpl(), LedgerStorageMemoryImpl()
    gate = BudgetGateImpl(budgets, ledger, BudgetGateOptions())
    ctx = context(Role.MEMBER)
    session = new_id()
    await budgets.create_budget(
        ctx.org_id, make_budget(BudgetScopeKind.SESSION, str(session), cost_micros=1_500), ()
    )
    first = await gate.authorize(ctx, request(ctx, scope(BudgetScopeKind.SESSION, session)))
    second = await gate.authorize(ctx, request(ctx, scope(BudgetScopeKind.SESSION, session)))
    assert isinstance(first, Hold) and isinstance(second, Refusal)
