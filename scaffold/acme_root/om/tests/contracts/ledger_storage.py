"""The ledger contract: a hold is held against every line before the call, a
refusal lists every breach and writes nothing, two holds over one line never
both pass when only one fits, and a hold closes once. The cases named in
`CROSS_TENANT_CASES` are the tenant fence's evidence: each one presents
another tenant's identifier and asserts that nothing is found and nothing
changes."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from acme.om.base import new_id, utcnow
from acme.om.budgets.rules import settlement_of
from acme.om.budgets.storage import LedgerStorageInterface
from acme.om.budgets.types.amount import Amount, AmountUnit, Spend
from acme.om.budgets.types.breach import BreachAction, Refusal
from acme.om.budgets.types.budget import BudgetScope, BudgetScopeKind
from acme.om.budgets.types.hold import (
    Billed,
    BillUnknown,
    Hold,
    HoldLine,
    NotBilled,
    NotBilledProof,
    Tally,
)
from acme.om.budgets.types.usage import UsageRecord, UsageRollup
from acme.om.exceptions import NotFound, TenantMismatch
from contracts.racing import race

CROSS_TENANT_CASES: frozenset[str] = frozenset(
    {
        "append_usage_record",
        "close_hold",
        "count_tenant",
        "open_hold",
        "read_hold",
        "read_settlement",
        "read_tally",
        "read_usage_records",
        "read_usage_rollups",
        "read_usage_total",
    }
)
"""Every method of `LedgerStorageInterface` that takes a tenant has a case in
this module that presents another tenant's."""

DAY = datetime(2026, 10, 2, tzinfo=UTC)


def a_line(
    *,
    cost_micros: int | None = 1_000,
    tokens: int | None = None,
    budget_id: UUID | None = None,
    window_start: datetime = DAY,
) -> HoldLine:
    return HoldLine(
        budget_id=budget_id or new_id(),
        scope=BudgetScope(kind=BudgetScopeKind.SESSION, key="s-1"),
        window_start=window_start,
        resets_at=window_start + timedelta(days=1),
        amount=Amount(cost_micros=cost_micros, tokens=tokens),
    )


def a_hold(
    *lines: HoldLine,
    cost_micros: int | None = 600,
    tokens: int = 100,
    own: Amount | None = None,
) -> Hold:
    return Hold(
        id=new_id(),
        created_at=utcnow(),
        spender_id=new_id(),
        session_id=new_id(),
        purpose="main",
        exposure=Spend(cost_micros=cost_micros, tokens=tokens),
        own=own,
        lines=lines,
    )


def a_record(
    session_id: UUID,
    loop_id: UUID,
    *,
    hold_id: UUID | None = None,
    cost_micros: int | None = 300,
    input_tokens: int = 1_000,
) -> UsageRecord:
    return UsageRecord(
        id=new_id(),
        created_at=utcnow(),
        hold_id=hold_id or new_id(),
        session_id=session_id,
        tree_id=session_id,
        loop_id=loop_id,
        step_id=new_id(),
        agent_kind="assistant",
        kind_version=1,
        role="main",
        provider="anthropic",
        model="claude-sonnet",
        input_tokens=input_tokens,
        cache_read_tokens=200,
        cache_write_tokens=100,
        output_tokens=50,
        thinking_tokens=10,
        cost_micros=cost_micros,
        latency_ms=1_000,
    )


async def held(storage: LedgerStorageInterface, org: UUID, line: HoldLine) -> Tally:
    found = await storage.read_tally(org, line.budget_id, line.window_start)
    return found or Tally(budget_id=line.budget_id, window_start=line.window_start)


class LedgerStorageContract:
    @pytest.fixture
    def storage(self) -> LedgerStorageInterface:
        raise NotImplementedError("the concrete test class provides the storage")

    async def test_a_hold_that_fits_is_held_on_every_line(
        self, storage: LedgerStorageInterface
    ) -> None:
        org = new_id()
        cost, tokens = a_line(cost_micros=1_000), a_line(cost_micros=None, tokens=500)
        hold = a_hold(cost, tokens)
        assert await storage.open_hold(org, hold) is None
        assert await storage.read_hold(org, hold.id) == hold
        assert (await held(storage, org, cost)).held_cost_micros == 600
        assert (await held(storage, org, tokens)).held_tokens == 100
        assert await storage.read_hold(org, new_id()) is None

    async def test_a_refusal_lists_every_breach_and_holds_nothing(
        self, storage: LedgerStorageInterface
    ) -> None:
        org = new_id()
        roomy = a_line(cost_micros=10_000)
        tight = a_line(cost_micros=500, tokens=50)
        unpriced = a_line(cost_micros=None, tokens=50)
        hold = a_hold(roomy, tight, unpriced, own=Amount(tokens=80))
        refusal = await storage.open_hold(org, hold)
        assert isinstance(refusal, Refusal)
        breached = {(b.budget_id, b.unit) for b in refusal.breaches}
        assert breached == {
            (tight.budget_id, AmountUnit.COST),
            (tight.budget_id, AmountUnit.TOKENS),
            (unpriced.budget_id, AmountUnit.TOKENS),
            (None, AmountUnit.TOKENS),
        }
        assert all(b.action is BreachAction.RAISE for b in refusal.breaches)
        first = next(b for b in refusal.breaches if b.budget_id == tight.budget_id)
        assert (first.needed, first.resets_at) == (600, tight.resets_at)
        assert await storage.read_hold(org, hold.id) is None
        for line in (roomy, tight, unpriced):
            tally = await held(storage, org, line)
            assert (tally.held_cost_micros, tally.held_tokens) == (0, 0)

    async def test_a_cost_no_price_gives_is_refused_where_cost_is_bounded(
        self, storage: LedgerStorageInterface
    ) -> None:
        org = new_id()
        cost, tokens = a_line(cost_micros=1_000_000), a_line(cost_micros=None, tokens=500)
        refusal = await storage.open_hold(org, a_hold(cost, tokens, cost_micros=None))
        assert isinstance(refusal, Refusal)
        (breach,) = refusal.breaches
        assert (breach.budget_id, breach.action) == (cost.budget_id, BreachAction.PRICE)
        # A token budget still binds when no price is known.
        assert await storage.open_hold(org, a_hold(tokens, cost_micros=None)) is None
        assert (await held(storage, org, tokens)).held_tokens == 100

    async def test_what_a_window_holds_and_spent_both_count(
        self, storage: LedgerStorageInterface
    ) -> None:
        org = new_id()
        line = a_line(cost_micros=1_000)
        first = a_hold(line, cost_micros=400)
        assert await storage.open_hold(org, first) is None
        settled = settlement_of(first, Billed(usage=Spend(cost_micros=300)), new_id(), utcnow())
        await storage.close_hold(org, settled)
        assert await storage.open_hold(org, a_hold(line, cost_micros=400)) is None
        # 300 spent and 400 held leave 300: a hold of 301 does not fit.
        refusal = await storage.open_hold(org, a_hold(line, cost_micros=301))
        assert isinstance(refusal, Refusal)
        assert refusal.breaches[0].committed == 700
        assert await storage.open_hold(org, a_hold(line, cost_micros=300)) is None

    async def test_each_window_counts_on_its_own(self, storage: LedgerStorageInterface) -> None:
        org, budget_id = new_id(), new_id()
        today = a_line(cost_micros=600, budget_id=budget_id)
        tomorrow = a_line(cost_micros=600, budget_id=budget_id, window_start=DAY + timedelta(1))
        assert await storage.open_hold(org, a_hold(today)) is None
        assert isinstance(await storage.open_hold(org, a_hold(today)), Refusal)
        assert await storage.open_hold(org, a_hold(tomorrow)) is None

    async def test_a_hold_opened_twice_is_held_once(self, storage: LedgerStorageInterface) -> None:
        org = new_id()
        line = a_line(cost_micros=1_000)
        hold = a_hold(line)
        assert await storage.open_hold(org, hold) is None
        assert await storage.open_hold(org, hold) is None
        assert (await held(storage, org, line)).held_cost_micros == 600

    async def test_two_holds_over_one_line_never_both_pass_when_one_fits(
        self, storage: LedgerStorageInterface
    ) -> None:
        org = new_id()
        line = a_line(cost_micros=1_000)

        async def one() -> Hold | None:
            hold = a_hold(line, cost_micros=600)
            return hold if await storage.open_hold(org, hold) is None else None

        run = await race(one(), one())
        assert len(run.admitted) == 1, run.summary()
        assert (await held(storage, org, line)).held_cost_micros == 600

    async def test_a_hold_closes_once_and_moves_its_lines(
        self, storage: LedgerStorageInterface
    ) -> None:
        org = new_id()
        line = a_line(cost_micros=1_000, tokens=1_000)
        hold = a_hold(line, cost_micros=600, tokens=100)
        assert await storage.open_hold(org, hold) is None
        first = settlement_of(
            hold, Billed(usage=Spend(cost_micros=250, tokens=40)), new_id(), utcnow()
        )
        assert await storage.close_hold(org, first) == first
        tally = await held(storage, org, line)
        assert (tally.held_cost_micros, tally.held_tokens) == (0, 0)
        assert (tally.spent_cost_micros, tally.spent_tokens) == (250, 40)
        second = settlement_of(hold, BillUnknown(), new_id(), utcnow())
        assert await storage.close_hold(org, second) == first
        assert await held(storage, org, line) == tally
        assert await storage.read_settlement(org, hold.id) == first

    async def test_a_release_spends_nothing_and_an_unknown_bill_the_whole_hold(
        self, storage: LedgerStorageInterface
    ) -> None:
        org = new_id()
        line = a_line(cost_micros=10_000)
        released, unknown = a_hold(line), a_hold(line)
        for hold in (released, unknown):
            assert await storage.open_hold(org, hold) is None
        proof = NotBilled(proof=NotBilledProof.REFUSED_BEFORE_PROCESSING)
        await storage.close_hold(org, settlement_of(released, proof, new_id(), utcnow()))
        await storage.close_hold(org, settlement_of(unknown, BillUnknown(), new_id(), utcnow()))
        tally = await held(storage, org, line)
        assert (tally.held_cost_micros, tally.spent_cost_micros) == (0, 600)

    async def test_close_hold_of_a_hold_never_opened_is_not_found(
        self, storage: LedgerStorageInterface
    ) -> None:
        hold = a_hold(a_line())
        with pytest.raises(NotFound):
            await storage.close_hold(
                new_id(), settlement_of(hold, BillUnknown(), new_id(), utcnow())
            )
        assert await storage.read_settlement(new_id(), hold.id) is None

    async def test_open_hold_under_another_tenant_is_refused(
        self, storage: LedgerStorageInterface
    ) -> None:
        org_a, org_b = new_id(), new_id()
        line = a_line(cost_micros=1_000)
        hold = a_hold(line)
        assert await storage.open_hold(org_a, hold) is None
        with pytest.raises(TenantMismatch):
            await storage.open_hold(org_b, hold)
        assert await storage.read_hold(org_b, hold.id) is None
        assert await storage.read_tally(org_b, line.budget_id, line.window_start) is None
        assert (await held(storage, org_a, line)).held_cost_micros == 600

    async def test_another_tenant_reads_and_closes_nothing(
        self, storage: LedgerStorageInterface
    ) -> None:
        org_a, org_b = new_id(), new_id()
        line = a_line(cost_micros=1_000)
        hold = a_hold(line)
        assert await storage.open_hold(org_a, hold) is None
        with pytest.raises(NotFound):
            await storage.close_hold(org_b, settlement_of(hold, BillUnknown(), new_id(), utcnow()))
        assert await storage.read_settlement(org_b, hold.id) is None
        assert await storage.read_settlement(org_a, hold.id) is None
        assert await storage.read_tally(org_b, line.budget_id, line.window_start) is None
        assert (await held(storage, org_a, line)).held_cost_micros == 600

    async def test_count_tenant_counts_the_tenants_ledger_up_to_its_limit(
        self, storage: LedgerStorageInterface
    ) -> None:
        org_a, org_b = new_id(), new_id()
        assert await storage.count_tenant(org_a, 10) == 0
        line = a_line(cost_micros=10_000)
        hold = a_hold(line)
        assert await storage.open_hold(org_a, hold) is None
        await storage.close_hold(org_a, settlement_of(hold, BillUnknown(), new_id(), utcnow()))
        # The hold, its settlement, and the line's tally.
        assert await storage.count_tenant(org_a, 10) == 3
        assert await storage.count_tenant(org_a, 2) == 2
        assert await storage.count_tenant(org_b, 10) == 0

    async def test_a_usage_record_is_written_once_per_hold(
        self, storage: LedgerStorageInterface
    ) -> None:
        org, session, loop = new_id(), new_id(), new_id()
        record = a_record(session, loop)
        assert await storage.append_usage_record(org, record) is True
        again = a_record(session, loop, hold_id=record.hold_id)
        assert await storage.append_usage_record(org, again) is False, "its hold has one"
        assert await storage.read_usage_records(org, session, None, 10) == [record]

    async def test_a_sessions_usage_pages_by_id_and_rolls_up_per_loop(
        self, storage: LedgerStorageInterface
    ) -> None:
        org, session, other = new_id(), new_id(), new_id()
        first, second = new_id(), new_id()
        records = [
            a_record(session, first, input_tokens=1_000),
            a_record(session, second, input_tokens=2_000),
            a_record(session, first, input_tokens=3_000, cost_micros=None),
        ]
        for record in records:
            assert await storage.append_usage_record(org, record)
        assert await storage.append_usage_record(org, a_record(other, first))
        assert await storage.read_usage_records(org, session, None, 10) == records
        page = await storage.read_usage_records(org, session, None, 2)
        assert page == records[:2]
        assert await storage.read_usage_records(org, session, page[-1].id, 2) == records[2:]
        loops = await storage.read_usage_rollups(org, session, 10)
        assert [(u.loop_id, u.rollup.calls, u.rollup.input_tokens) for u in loops] == [
            (first, 2, 4_000),
            (second, 1, 2_000),
        ], "in the order each loop first called"
        assert [u.loop_id for u in await storage.read_usage_rollups(org, session, 1)] == [first]
        assert await storage.read_usage_total(org, session) == UsageRollup(
            calls=3,
            input_tokens=6_000,
            cache_read_tokens=600,
            cache_write_tokens=300,
            output_tokens=150,
            thinking_tokens=30,
            cost_micros=600,
            unpriced=1,
            latency_ms=3_000,
        ), "a call no price applied to is unpriced, and in no cost"
        assert await storage.read_usage_total(org, new_id()) == UsageRollup()

    async def test_another_tenant_reads_no_usage_and_cannot_take_a_records_id(
        self, storage: LedgerStorageInterface
    ) -> None:
        org_a, org_b, session, loop = new_id(), new_id(), new_id(), new_id()
        record = a_record(session, loop)
        assert await storage.append_usage_record(org_a, record)
        with pytest.raises(TenantMismatch):
            await storage.append_usage_record(org_b, record)
        assert await storage.read_usage_records(org_b, session, None, 10) == []
        assert await storage.read_usage_rollups(org_b, session, 10) == []
        assert await storage.read_usage_total(org_b, session) == UsageRollup()
        assert await storage.read_usage_records(org_a, session, None, 10) == [record]

    async def test_count_tenant_never_counts_usage_records(
        self, storage: LedgerStorageInterface
    ) -> None:
        """A record outlives its tenant as content-free billing data, and never
        keeps the tenant from being marked purged."""
        org = new_id()
        assert await storage.append_usage_record(org, a_record(new_id(), new_id()))
        assert await storage.count_tenant(org, 10) == 0
