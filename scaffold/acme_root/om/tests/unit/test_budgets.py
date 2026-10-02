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
from acme.om.budgets.pricing import ModelPrice, PriceTier, Rates
from acme.om.budgets.rules import (
    EPOCH,
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
from acme.om.budgets.types.exposure import CallShape, PromptCount, PromptSize
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
BASE = Rates(input=3 * MILLION, cache_write=3_750_000, cache_read=300_000, output=15 * MILLION)
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
cache, 15 out; past 200k prompt tokens `long` doubles its input and raises
its output by half; `thinker` bills thinking at a rate of its own."""


def shape(
    prompt: int,
    output: int,
    *,
    cache: bool = False,
    thinking: int = 0,
    tools: dict[str, int] | None = None,
) -> CallShape:
    return CallShape(
        prompt=PromptSize(tokens=prompt, counted_by=PromptCount.PROVIDER),
        writes_cache=cache,
        output_bound=output,
        thinking_outside=thinking,
        provider_tools=tools or {},
    )


@pytest.mark.parametrize(
    ("model", "call", "cost"),
    [
        # 10k in at 3, 1k out at 15.
        ("plain", shape(10_000, 1_000), 30_000 + 15_000),
        # A request that writes the cache: every prompt token at the write rate.
        ("plain", shape(10_000, 1_000, cache=True), 37_500 + 15_000),
        # Below the tier, the base rates; past it, the tier's, the output's too.
        ("long", shape(150_000, 4_000), 450_000 + 60_000),
        ("long", shape(250_000, 4_000), 1_500_000 + 90_000),
        ("long", shape(250_000, 4_000, cache=True), 1_875_000 + 90_000),
        # Thinking at its own rate, inside the output bound and outside it.
        ("thinker", shape(1_000, 2_000, thinking=8_000), 1_250 + 24_000 + 96_000),
        # The provider's own tool: its fee times the most calls it may make.
        ("searcher", shape(10_000, 1_000, tools={"web_search": 5}), 45_000 + 50_000),
        ("free", shape(10_000, 1_000), 0),
    ],
)
def test_the_hold_covers_the_worst_case(model: str, call: CallShape, cost: int) -> None:
    exposure = call_exposure(call, PRICES[model])
    assert exposure.cost_micros == cost
    assert exposure.tokens == call.prompt.tokens + call.output_bound + call.thinking_outside


def billed_at(price: ModelPrice, prompt: tuple[int, int, int], output: int, thinking: int) -> int:
    """What a usage costs at the rates that apply to it: the prompt split into
    plain, cache read, and cache write tokens."""
    plain, read, written = prompt
    tiers = [t for t in price.tiers if sum(prompt) > t.above]
    rates = tiers[-1].rates if tiers else price.rates
    per_token = (
        plain * rates.input
        + read * rates.cache_read
        + written * rates.cache_write
        + output * rates.output
        + thinking * (rates.output if rates.thinking is None else rates.thinking)
    )
    return -(-per_token // MILLION)


@pytest.mark.parametrize("model", ["plain", "long", "thinker"])
def test_no_usage_the_shape_allows_costs_more_than_its_hold(model: str) -> None:
    """Whatever mix of plain, cached, and written prompt tokens the provider
    reports, and whatever output and thinking up to their bounds, the usage
    costs no more than the hold."""
    price = PRICES[model]
    for size, cache in product((1_000, 199_999, 200_001, 300_000), (False, True)):
        call = shape(size, 4_000, cache=cache, thinking=2_000)
        hold = call_exposure(call, price).cost_micros
        assert hold is not None
        splits = [(size, 0, 0), (0, size, 0)] + (
            [(0, 0, size), (size // 2, 0, size - size // 2)] if cache else []
        )
        # The output bound written as text or as thinking, and thinking beyond it.
        turns = [(0, 0), (4_000, 0), (4_000, 2_000), (0, 6_000)]
        for prompt, (output, thinking) in product(splits, turns):
            assert billed_at(price, prompt, output, thinking) <= hold, (model, size, prompt)


def test_with_no_price_the_cost_is_unknown_and_the_tokens_still_count() -> None:
    assert call_exposure(shape(10_000, 1_000), None) == Spend(cost_micros=None, tokens=11_000)
    unpriced_tool = shape(10_000, 1_000, tools={"code_run": 1})
    assert call_exposure(unpriced_tool, PRICES["searcher"]).cost_micros is None


def test_a_prompt_size_is_the_providers_count_or_a_proven_bound_and_a_tool_has_a_bound() -> None:
    assert PromptSize(tokens=5, counted_by=PromptCount.UPPER_BOUND).tokens == 5
    with pytest.raises(ValidationError):
        PromptSize.model_validate({"tokens": 5, "counted_by": "estimate"})
    unbounded = {"prompt": {"tokens": 10, "counted_by": "provider"}, "output_bound": 10}
    with pytest.raises(ValidationError):
        CallShape.model_validate({**unbounded, "provider_tools": {"web_search": None}})


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
    budget_id: UUID | None, resets_at: datetime | None, action: BreachAction = BreachAction.RAISE
) -> Breach:
    return Breach(
        budget_id=budget_id,
        scope=None if budget_id is None else BudgetScope(kind=BudgetScopeKind.SESSION, key="s"),
        unit=AmountUnit.COST,
        amount=1,
        committed=1,
        exposure=None if action is BreachAction.PRICE else 1,
        action=action,
        needed=None if action is BreachAction.PRICE else 2,
        resets_at=resets_at,
    )


def test_a_budget_parks_until_its_last_window_resets_or_a_person_raises_it() -> None:
    day, month = new_id(), new_id()
    tomorrow, next_month = AT + timedelta(days=1), AT + timedelta(days=24)
    both = Refusal(breaches=(breach(month, next_month), breach(day, tomorrow)))
    park = budget_park(both)
    assert (park.reason, park.unlock, park.retry_at) == (ParkReason.BUDGET, str(month), next_month)
    life = new_id()
    forever = budget_park(Refusal(breaches=(breach(day, tomorrow), breach(life, None))))
    assert (forever.unlock, forever.retry_at) == (str(life), None)
    own = budget_park(Refusal(breaches=(breach(None, None),)))
    assert (own.unlock, own.retry_at) == (OWN_AMOUNT_UNLOCK, None)
    unpriced = budget_park(Refusal(breaches=(breach(day, tomorrow, BreachAction.PRICE),)))
    assert (unpriced.unlock, unpriced.retry_at) == (PRICE_UNLOCK, None)


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
