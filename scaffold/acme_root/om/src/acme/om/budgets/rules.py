"""Pure rules of budgets: the window a time falls in, a call's worst case, the
breaches of a hold, how a hold settles, and the park a refusal asks for.
Values in, values out; no clock, no storage. Both ledger impls ask
`breaches` under their lock before they write."""

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID

from acme.om.budgets.pricing import ModelPrice, Rates
from acme.om.budgets.types.amount import NOTHING, Amount, AmountUnit, Spend
from acme.om.budgets.types.breach import Breach, BreachAction, Refusal
from acme.om.budgets.types.budget import Budget, BudgetWindow, WindowKind
from acme.om.budgets.types.exposure import CacheWrite, CallShape
from acme.om.budgets.types.hold import (
    Bill,
    Billed,
    BillUnknown,
    Hold,
    HoldLine,
    NotBilled,
    Settlement,
    Tally,
)
from acme.om.steps.types.header import Park, ParkReason

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
"""The start of every window that never resets, and the origin a span
counts from."""

PER_MILLION = 1_000_000

PRICE_UNLOCK = "price"
"""A budget park's unlock when no price bounds the call's cost."""

OWN_AMOUNT_UNLOCK = "own_amount"
"""A budget park's unlock when the call passes the request's own amount."""

HOLD_RETRY = timedelta(seconds=30)
"""How soon a loop refused only by open holds asks again: a call's hold
settles when the call ends, and a settlement frees the room it held."""

TallyKey = tuple[UUID, datetime]
"""A line's tally: its budget and the start of its window."""


# Windows.


def window_bounds(window: BudgetWindow, now: datetime) -> tuple[datetime, datetime | None]:
    """The start of the window `now` falls in, and when it resets: None for a
    window that never does. Every window is counted in UTC, and a week starts
    on Monday."""
    at = now.astimezone(UTC)
    match window.kind:
        case WindowKind.LIFE:
            return EPOCH, None
        case WindowKind.HOUR:
            start = at.replace(minute=0, second=0, microsecond=0)
            return start, start + timedelta(hours=1)
        case WindowKind.DAY:
            start = at.replace(hour=0, minute=0, second=0, microsecond=0)
            return start, start + timedelta(days=1)
        case WindowKind.WEEK:
            day = at.replace(hour=0, minute=0, second=0, microsecond=0)
            start = day - timedelta(days=day.weekday())
            return start, start + timedelta(weeks=1)
        case WindowKind.MONTH:
            start = at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if start.month == 12:
                return start, start.replace(year=start.year + 1, month=1)
            return start, start.replace(month=start.month + 1)
        case WindowKind.SPAN:
            span = timedelta(seconds=window.seconds or 1)
            start = EPOCH + span * ((at - EPOCH) // span)
            return start, start + span


def lines_of(budgets: Iterable[Budget], now: datetime) -> tuple[HoldLine, ...]:
    """Each budget as a line of a hold made at `now`, in the order the ledger
    locks them: by budget, so two holds over the same lines never wait on
    each other crosswise."""
    lines = []
    for budget in sorted(budgets, key=lambda b: b.id):
        start, resets_at = window_bounds(budget.window, now)
        lines.append(
            HoldLine(
                budget_id=budget.id,
                scope=budget.scope,
                window_start=start,
                resets_at=resets_at,
                amount=budget.amount,
            )
        )
    return tuple(lines)


def key_of(line: HoldLine) -> TallyKey:
    return (line.budget_id, line.window_start)


# The worst case.


def _ceil(tokens: int, rate: int, per: int = PER_MILLION) -> int:
    """Tokens at a rate per `per`, rounded up: a hold never falls short of a
    figure by its rounding."""
    return -(-tokens * rate // per)


def _highest(price: ModelPrice, input_tokens: int) -> Rates:
    """The highest of each rate that can apply to a call of this much input:
    the base rates and every tier it passes. A tier raises the output
    rate too, so each rate is taken at its highest across them."""
    applying = [price.rates, *(t.rates for t in price.tiers if input_tokens > t.above)]
    long_writes = [r.cache_write_long for r in applying]
    return Rates(
        input=max(r.input for r in applying),
        cache_write=max(r.cache_write for r in applying),
        cache_read=max(r.cache_read for r in applying),
        output=max(r.output for r in applying),
        # A long cache's write has a rate only where every rate that can
        # apply names one.
        cache_write_long=None if None in long_writes else max(w or 0 for w in long_writes),
        thinking=max(r.output if r.thinking is None else r.thinking for r in applying),
    )


def call_exposure(shape: CallShape, price: ModelPrice | None) -> Spend:
    """A model call's worst case: every input token at the highest input rate
    that can apply (the write of the cache the request writes, the tier the
    input can pass), the whole output bound at the highest rate a token
    inside it can be billed at (text, or thinking the bound holds), thinking
    billed outside that bound at the highest thinking rate, and each provider
    tool's fee times the most calls it may make. The input is the prompt and
    the most the provider's tools may add, which both providers bill as input
    and which can carry a call past a tier. Its tokens are the input, the
    output bound, and the thinking beyond it. With no price, a long cache
    the price names no write for, or a provider tool it names no fee for,
    the cost is unknown."""
    inputs = shape.input_bound
    tokens = inputs + shape.output_bound + shape.thinking_outside
    if price is None:
        return Spend(cost_micros=None, tokens=tokens)
    if any(tool.name not in price.tool_fees for tool in shape.provider_tools):
        return Spend(cost_micros=None, tokens=tokens)
    rates = _highest(price, inputs)
    match shape.cache:
        case CacheWrite.NONE:
            input_rate = rates.input
        case CacheWrite.SHORT:
            input_rate = max(rates.input, rates.cache_write)
        case CacheWrite.LONG:
            if rates.cache_write_long is None:
                return Spend(cost_micros=None, tokens=tokens)
            input_rate = max(rates.input, rates.cache_write_long)
    thinking_rate = rates.output if rates.thinking is None else rates.thinking
    cost = (
        _ceil(inputs, input_rate)
        + _ceil(shape.output_bound, max(rates.output, thinking_rate))
        + _ceil(shape.thinking_outside, thinking_rate)
        + sum(int(price.tool_fees[tool.name]) * tool.calls for tool in shape.provider_tools)
    )
    return Spend(cost_micros=cost, tokens=tokens)


def job_exposure(rate_micros_per_hour: int, now: datetime, deadline: datetime) -> Spend:
    """A spending job's worst case: its rate times the time left to its
    deadline, rounded up to the second. A job never starts past its deadline,
    so a deadline that has passed is refused here."""
    if rate_micros_per_hour < 0:
        raise ValueError("a job's rate is not negative")
    left = deadline - now
    if left <= timedelta(0):
        raise ValueError("a job never starts after its deadline")
    seconds = -(-left // timedelta(seconds=1))
    return Spend(cost_micros=_ceil(seconds, rate_micros_per_hour, 3600), tokens=0)


# Breaches.


def breaches(hold: Hold, tallies: Mapping[TallyKey, Tally]) -> tuple[Breach, ...]:
    """Every unit of every line the hold does not fit under, beside what the
    line's window spent and holds already, and every unit of the request's own
    amount its worst case passes. A line that bounds cost refuses a hold whose
    cost is unknown. Empty when the hold fits everywhere."""
    found: list[Breach] = []
    for line in hold.lines:
        tally = tallies.get(key_of(line)) or Tally(
            budget_id=line.budget_id, window_start=line.window_start
        )
        for unit in AmountUnit:
            amount = line.amount.of(unit)
            if amount is None:
                continue
            breach = _breach(amount, committed_of(tally, unit), hold.exposure.of(unit))
            if breach is not None:
                action, needed = breach
                found.append(
                    Breach(
                        budget_id=line.budget_id,
                        scope=line.scope,
                        unit=unit,
                        amount=amount,
                        committed=committed_of(tally, unit),
                        held=held_of(tally, unit),
                        exposure=hold.exposure.of(unit),
                        action=action,
                        needed=needed,
                        resets_at=line.resets_at,
                    )
                )
    if hold.own is not None:
        for unit in AmountUnit:
            amount = hold.own.of(unit)
            if amount is None:
                continue
            breach = _breach(amount, 0, hold.exposure.of(unit))
            if breach is not None:
                action, needed = breach
                found.append(
                    Breach(
                        budget_id=None,
                        scope=None,
                        unit=unit,
                        amount=amount,
                        committed=0,
                        held=0,
                        exposure=hold.exposure.of(unit),
                        action=action,
                        needed=needed,
                        resets_at=None,
                    )
                )
    return tuple(found)


def _breach(
    amount: int, committed: int, exposure: int | None
) -> tuple[BreachAction, int | None] | None:
    if exposure is None:
        return BreachAction.PRICE, None
    if committed + exposure > amount:
        return BreachAction.RAISE, committed + exposure
    return None


def committed_of(tally: Tally, unit: AmountUnit) -> int:
    """What a window spent and holds already, in one unit."""
    if unit is AmountUnit.COST:
        return tally.spent_cost_micros + tally.held_cost_micros
    return tally.spent_tokens + tally.held_tokens


def held_of(tally: Tally, unit: AmountUnit) -> int:
    """What calls not yet settled hold in a window, in one unit."""
    return tally.held_cost_micros if unit is AmountUnit.COST else tally.held_tokens


def refusal_of(hold: Hold, tallies: Mapping[TallyKey, Tally]) -> Refusal | None:
    found = breaches(hold, tallies)
    return Refusal(breaches=found) if found else None


# The tallies, as both ledgers move them.


def opened(tally: Tally, exposure: Spend) -> Tally:
    """The tally once a hold reserves its worst case on it."""
    return tally.model_copy(
        update={
            "held_cost_micros": tally.held_cost_micros + (exposure.cost_micros or 0),
            "held_tokens": tally.held_tokens + exposure.tokens,
        }
    )


def closed(tally: Tally, exposure: Spend, spent: Spend) -> Tally:
    """The tally once a settlement closes a hold on it: the hold leaves what
    it holds, and what it spent joins what the window spent."""
    return tally.model_copy(
        update={
            "held_cost_micros": max(0, tally.held_cost_micros - (exposure.cost_micros or 0)),
            "held_tokens": max(0, tally.held_tokens - exposure.tokens),
            "spent_cost_micros": tally.spent_cost_micros + (spent.cost_micros or 0),
            "spent_tokens": tally.spent_tokens + spent.tokens,
        }
    )


# Settlement.


def spent_by(hold: Hold, bill: Bill) -> Spend:
    """What a hold settles at. Released, at nothing, only when the provider
    provably did not bill; at the usage reported or retrieved, its cost at
    the hold's when no price gave one; and at the whole hold when the usage is
    unknown."""
    match bill:
        case NotBilled():
            return NOTHING
        case Billed(usage=usage):
            if usage.cost_micros is None:
                return Spend(cost_micros=hold.exposure.cost_micros, tokens=usage.tokens)
            return usage
        case BillUnknown():
            return hold.exposure


def overshoot_of(hold: Hold, spent: Spend) -> Spend | None:
    """By how much a spend passed its hold, in each unit, or None when it
    stayed within it."""
    cost = 0
    if spent.cost_micros is not None and hold.exposure.cost_micros is not None:
        cost = max(0, spent.cost_micros - hold.exposure.cost_micros)
    tokens = max(0, spent.tokens - hold.exposure.tokens)
    if cost == 0 and tokens == 0:
        return None
    return Spend(cost_micros=cost, tokens=tokens)


def settlement_of(hold: Hold, bill: Bill, settlement_id: UUID, now: datetime) -> Settlement:
    spent = spent_by(hold, bill)
    return Settlement(
        id=settlement_id,
        created_at=now,
        hold_id=hold.id,
        bill=bill,
        spent=spent,
        overshoot=overshoot_of(hold, spent),
    )


# The budget itself.


def raises(before: Amount, after: Amount) -> bool:
    """Whether a change lets more through in some unit: a unit bounded before
    and higher or unbounded after. A raise is the instruction to continue."""
    for unit in AmountUnit:
        was, now = before.of(unit), after.of(unit)
        if was is not None and (now is None or now > was):
            return True
    return False


# The park a refusal asks for.


def budget_park(refusal: Refusal, now: datetime, hold_retry: timedelta = HOLD_RETRY) -> Park:
    """A guard parks: the loop waits until what refused it can have cleared.
    Each breach clears in one of three ways: soon, when the calls that hold
    room in its window settle, if what the window spent leaves room for the
    call; when its window resets, if the line's amount holds the call at all;
    or only by a person, who raises the amount or prices the model, when the
    call's worst case alone passes the amount, a window never resets, or no
    price bounds the call. The park tries again by itself when the last of
    its breaches can have cleared, and never when one waits on a person. It
    names the breach that binds longest."""
    clears = [(_clears_at(breach, now, hold_retry), breach) for breach in refusal.breaches]
    _, longest = max(clears, key=lambda pair: (pair[0] is None, pair[0] or EPOCH))
    if longest.action is BreachAction.PRICE:
        unlock = PRICE_UNLOCK
    elif longest.budget_id is None:
        unlock = OWN_AMOUNT_UNLOCK
    else:
        unlock = str(longest.budget_id)
    times = [at for at, _ in clears]
    retry_at = None if None in times else max(at for at in times if at is not None)
    return Park(reason=ParkReason.BUDGET, unlock=unlock, retry_at=retry_at)


def _clears_at(breach: Breach, now: datetime, hold_retry: timedelta) -> datetime | None:
    """The earliest a breach can clear without a person, or None when only
    a person clears it."""
    if breach.action is not BreachAction.RAISE or breach.budget_id is None:
        return None
    exposure = breach.exposure or 0
    if exposure > breach.amount:
        return None  # a fresh window refuses it again
    if breach.committed - breach.held + exposure <= breach.amount:
        # Open holds take the room; their settlements free it.
        soon = now + hold_retry
        return soon if breach.resets_at is None else min(soon, breach.resets_at)
    return breach.resets_at
