"""The models swimlane over the memory storage: a session resolves its fills
once, a switch is a new version announced by a `switched` step naming both
fills, and nothing a resolver can pick lacks a price row."""

from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from contracts.doubles import APP, context
from contracts.fill_set_storage import HAIKU, SOL, SONNET
from contracts.step_storage import make_message

from acme.infra.impl.local import InfraLocalImpl
from acme.integrations.model_providers.types import Effort, ProviderName, Usage
from acme.om.base import new_id
from acme.om.budgets.impl.pricing import LIST_PRICES, PricingTableImpl
from acme.om.budgets.pricing import Price, Rates
from acme.om.context import RequestContext, Role, TenantContext
from acme.om.exceptions import (
    NotAuthorized,
    NotFound,
    PreconditionFailed,
    StaleWriter,
    UnpricedModel,
    UnresolvedRole,
    ValidationFailed,
)
from acme.om.models import rules
from acme.om.models.impl.manager import ModelsManagerImpl, ModelsOptions
from acme.om.models.impl.resolver import (
    DEFAULT_TABLE,
    ModelResolverTableImpl,
    ResolverOptions,
    RoleTable,
)
from acme.om.models.storage.impl.memory import FillSetStorageMemoryImpl
from acme.om.models.types.fill import (
    MAIN,
    SUMMARIZER,
    Eligibility,
    Fill,
    FillSet,
    SwitchReason,
)
from acme.om.root import Managers, build_managers
from acme.om.steps.types.header import SwitchedHeader
from acme.om.steps.types.step import Step, StepType
from acme.om.storage.impl.memory import StorageMemoryImpl

UNLISTED = Fill(
    provider=ProviderName.ANTHROPIC,
    model="claude-unlisted",
    max_output_tokens=1_000,
    context_window=100_000,
)
REGIONAL = SOL.model_copy(update={"eligibility": Eligibility(region="eu", zero_retention=True)})
TABLE = {
    MAIN: RoleTable(fill=SONNET, fallbacks=(SOL, REGIONAL)),
    SUMMARIZER: RoleTable(fill=HAIKU),
}


@pytest.fixture
def managers(tmp_path: Path) -> Managers:
    return build_managers(StorageMemoryImpl(), InfraLocalImpl(tmp_path))


async def a_run(managers: Managers, ctx: TenantContext) -> tuple[UUID, int, UUID]:
    """A session with a loop open and a run holding it: its id, the run's
    epoch, and the loop's id."""
    session_id = new_id()
    (first,) = await managers.steps.append_inputs(ctx, session_id, [make_message(session_id)])
    epoch = await managers.steps.begin_run(ctx, session_id)
    return session_id, epoch, first.id


async def switches(managers: Managers, ctx: TenantContext, session_id: UUID) -> list[Step]:
    page = await managers.steps.get_steps(ctx, session_id, 0, 100)
    return [s for s in page.items if s.type is StepType.SWITCHED]


# The resolver and its one source of prices.


def test_the_resolver_refuses_a_model_with_no_price_row() -> None:
    pricing = PricingTableImpl()
    for table in (
        {MAIN: RoleTable(fill=UNLISTED)},
        {MAIN: RoleTable(fill=SONNET, fallbacks=(UNLISTED,))},
    ):
        with pytest.raises(UnpricedModel):
            ModelResolverTableImpl(pricing, ResolverOptions(table=table))
    resolver = ModelResolverTableImpl(pricing, ResolverOptions())
    with pytest.raises(UnpricedModel):
        resolver.check(UNLISTED)


def test_every_model_the_default_table_names_has_a_price_of_its_own() -> None:
    pricing = PricingTableImpl()
    for entry in DEFAULT_TABLE.values():
        for fill in (entry.fill, *entry.fallbacks):
            assert pricing.price(fill.provider, fill.model) is not None, fill.name
    with pytest.raises(ValueError, match="two rows"):
        PricingTableImpl((*LIST_PRICES, LIST_PRICES[0]))


async def test_a_role_resolves_within_the_sessions_eligibility() -> None:
    resolver = ModelResolverTableImpl(PricingTableImpl(), ResolverOptions(table=TABLE))
    ctx = context(Role.MEMBER)
    (main,) = await resolver.resolve(ctx, [MAIN], Eligibility(region="eu", zero_retention=True))
    assert (main.fill, main.fallbacks) == (REGIONAL, ()), "the one fill the session may run on"
    both = await resolver.resolve(ctx, [SUMMARIZER, MAIN, MAIN], Eligibility())
    assert [r.role for r in both] == [MAIN, SUMMARIZER]
    assert both[0].fallbacks == (SOL, REGIONAL)
    with pytest.raises(UnresolvedRole):
        await resolver.resolve(ctx, ["title"], Eligibility())
    with pytest.raises(UnresolvedRole):
        await resolver.resolve(ctx, [SUMMARIZER], Eligibility(zero_retention=True))


def test_a_price_costs_usage_in_its_classes_and_its_long_tier() -> None:
    rates = Rates(
        input=Decimal(2),
        cache_read=Decimal("0.2"),
        cache_write=Decimal("2.5"),
        output=Decimal(10),
        thinking=Decimal(10),
    )
    usage = Usage(
        input=1_000_000, cache_read=1_000_000, cache_write=0, output=100_000, thinking=100_000
    )
    assert rates.cost(usage) == Decimal("4.2")
    long = rates.model_copy(update={"input": Decimal(4)})
    price = Price(
        provider=ProviderName.OPENAI,
        model="m",
        rates=rates,
        long_context_above=1_500_000,
        long_context=long,
        as_of=LIST_PRICES[0].as_of,
    )
    assert price.cost(usage) == Decimal("6.2"), "two million prompt tokens pass the tier"
    assert price.rates_for(1_500_000) is rates and price.rates_for(1_500_001) is long
    assert price.highest is long
    with pytest.raises(ValueError):
        Price(
            provider=ProviderName.OPENAI,
            model="m",
            rates=rates,
            long_context_above=10,
            as_of=price.as_of,
        )


# A session's fill set, and its switches.


async def test_a_session_resolves_its_fills_once(managers: Managers) -> None:
    ctx = context(Role.MEMBER)
    session_id = new_id()
    with pytest.raises(NotFound):
        await managers.models.get_fill_set(ctx, session_id)
    first = await managers.models.resolve_fill_set(
        ctx, session_id, [MAIN, SUMMARIZER], Eligibility()
    )
    assert first.version == 1 and first.reason is None and first.switched_by is None
    assert first.fill_for(MAIN) == DEFAULT_TABLE[MAIN].fill
    again = await managers.models.resolve_fill_set(
        ctx, session_id, [MAIN], Eligibility(region="eu")
    )
    assert again == first, "a later resolution keeps the first"
    assert await managers.models.get_fill_set(ctx, session_id) == first
    with pytest.raises(NotFound):
        await managers.models.get_fill_set(context(Role.MEMBER), session_id)


async def test_a_switch_writes_a_new_version_and_a_switched_step_naming_both_fills(
    managers: Managers,
) -> None:
    ctx = context(Role.MEMBER)
    session_id, epoch, loop_id = await a_run(managers, ctx)
    first = await managers.models.resolve_fill_set(
        ctx, session_id, [MAIN, SUMMARIZER], Eligibility()
    )
    to = DEFAULT_TABLE[MAIN].fallbacks[0]
    second = await managers.models.switch_fill(
        ctx, session_id, epoch, loop_id, MAIN, to, SwitchReason.FALLBACK
    )
    (step,) = await switches(managers, ctx, session_id)
    assert isinstance(step.header, SwitchedHeader)
    named = step.header.fills
    assert (named.role, named.from_fill, named.to_fill) == (MAIN, first.fill_for(MAIN), to)
    assert (named.fill_set_version, named.reason) == (2, SwitchReason.FALLBACK)
    assert (second.version, second.switched_by, second.reason) == (
        2,
        step.id,
        SwitchReason.FALLBACK,
    )
    assert second.fill_for(MAIN) == to and second.fill_for(SUMMARIZER) == first.fill_for(SUMMARIZER)
    main = second.role_fill(MAIN)
    assert main is not None and to not in main.fallbacks
    assert await managers.models.get_fill_set(ctx, session_id) == second


async def test_a_run_that_lost_its_claim_switches_nothing(managers: Managers) -> None:
    ctx = context(Role.MEMBER)
    session_id, stale, loop_id = await a_run(managers, ctx)
    first = await managers.models.resolve_fill_set(ctx, session_id, [MAIN], Eligibility())
    await managers.steps.begin_run(ctx, session_id)
    with pytest.raises(StaleWriter):
        await managers.models.switch_fill(
            ctx, session_id, stale, loop_id, MAIN, SOL, SwitchReason.POLICY
        )
    assert await switches(managers, ctx, session_id) == []
    assert await managers.models.get_fill_set(ctx, session_id) == first


@pytest.mark.parametrize(
    "role,to,refused",
    [
        (MAIN, DEFAULT_TABLE[MAIN].fill, ValidationFailed),  # the fill it holds
        ("title", SOL, ValidationFailed),  # a role the set does not hold
        (MAIN, UNLISTED, UnpricedModel),
    ],
)
async def test_a_refused_switch_writes_nothing(
    managers: Managers, role: str, to: Fill, refused: type[Exception]
) -> None:
    ctx = context(Role.MEMBER)
    session_id, epoch, loop_id = await a_run(managers, ctx)
    first = await managers.models.resolve_fill_set(ctx, session_id, [MAIN], Eligibility())
    with pytest.raises(refused):
        await managers.models.switch_fill(
            ctx, session_id, epoch, loop_id, role, to, SwitchReason.POLICY
        )
    assert await switches(managers, ctx, session_id) == []
    assert await managers.models.get_fill_set(ctx, session_id) == first


async def test_a_switch_stays_inside_the_sessions_eligibility(managers: Managers) -> None:
    ctx = context(Role.MEMBER)
    session_id, epoch, loop_id = await a_run(managers, ctx)
    ineligible = SOL.model_copy(update={"eligibility": Eligibility(region="us")})
    eligible = SOL.model_copy(update={"eligibility": Eligibility(region="eu")})
    lighter = eligible.model_copy(update={"effort": Effort.LOW})
    pricing = PricingTableImpl()
    models = ModelsManagerImpl(
        FillSetStorageMemoryImpl(),
        managers.steps,
        managers.tenancy,
        ModelResolverTableImpl(
            pricing,
            ResolverOptions(
                table={MAIN: RoleTable(fill=eligible, fallbacks=(ineligible, lighter))}
            ),
        ),
        ModelsOptions(),
    )
    await models.resolve_fill_set(ctx, session_id, [MAIN], Eligibility(region="eu"))
    with pytest.raises(ValidationFailed):
        await models.switch_fill(
            ctx, session_id, epoch, loop_id, MAIN, ineligible, SwitchReason.POLICY
        )
    fallen = await models.fall_back(ctx, session_id, epoch, loop_id, MAIN)
    assert fallen is not None and fallen.fill_for(MAIN) == lighter, "the ineligible one is passed"
    assert await models.fall_back(ctx, session_id, epoch, loop_id, MAIN) is None, "none is left"
    assert len(await switches(managers, ctx, session_id)) == 1


async def test_a_fallback_takes_the_next_declared_fill_it_has_not_tried(managers: Managers) -> None:
    ctx = context(Role.MEMBER)
    session_id, epoch, loop_id = await a_run(managers, ctx)
    head = await managers.models.resolve_fill_set(ctx, session_id, [MAIN], Eligibility())
    declared = DEFAULT_TABLE[MAIN].fallbacks
    assert rules.next_fallback(head, MAIN) == declared[0]
    assert rules.next_fallback(head, MAIN, tried=declared) is None
    assert rules.next_fallback(head, "title") is None
    fallen = await managers.models.fall_back(ctx, session_id, epoch, loop_id, MAIN)
    assert fallen is not None and fallen.fill_for(MAIN) == declared[0]
    assert fallen.reason is SwitchReason.FALLBACK


class CrashAfterTheStep(FillSetStorageMemoryImpl):
    """A storage whose first write of a later version is lost, as a crash
    between the step and the version would lose it."""

    def __init__(self) -> None:
        super().__init__()
        self.crashed = False

    async def write_fill_set(self, org_id: UUID, fill_set: FillSet) -> bool:
        if fill_set.version > 1 and not self.crashed:
            self.crashed = True
            raise ConnectionError("the process went away")
        return await super().write_fill_set(org_id, fill_set)


async def test_a_version_a_crash_left_unwritten_is_written_from_its_step(
    managers: Managers,
) -> None:
    ctx = context(Role.MEMBER)
    session_id, epoch, loop_id = await a_run(managers, ctx)
    storage = CrashAfterTheStep()
    models = ModelsManagerImpl(
        storage,
        managers.steps,
        managers.tenancy,
        ModelResolverTableImpl(PricingTableImpl(), ResolverOptions()),
        ModelsOptions(),
    )
    await models.resolve_fill_set(ctx, session_id, [MAIN], Eligibility())
    with pytest.raises(ConnectionError):
        await models.switch_fill(ctx, session_id, epoch, loop_id, MAIN, SOL, SwitchReason.UPGRADE)
    (step,) = await switches(managers, ctx, session_id)
    assert (await models.get_fill_set(ctx, session_id)).version == 1, "the step is ahead"
    settled = await models.settle_switch(ctx, step)
    assert (settled.version, settled.switched_by, settled.fill_for(MAIN)) == (2, step.id, SOL)
    assert await models.settle_switch(ctx, step) == settled, "settled twice, written once"
    assert await models.get_fill_set(ctx, session_id) == settled
    with pytest.raises(ValidationFailed):
        await models.settle_switch(ctx, make_message(session_id))
    assert isinstance(step.header, SwitchedHeader)
    later = step.header.fills.model_copy(update={"fill_set_version": 4})
    ahead = step.model_copy(update={"id": new_id(), "header": SwitchedHeader(fills=later)})
    with pytest.raises(PreconditionFailed):
        await models.settle_switch(ctx, ahead)


async def test_a_viewer_reads_and_switches_nothing(managers: Managers) -> None:
    member = context(Role.MEMBER)
    session_id, epoch, loop_id = await a_run(managers, member)
    viewer = context(Role.VIEWER)
    with pytest.raises(NotAuthorized):
        await managers.models.resolve_fill_set(viewer, session_id, [MAIN], Eligibility())
    with pytest.raises(NotAuthorized):
        await managers.models.switch_fill(
            viewer, session_id, epoch, loop_id, MAIN, SOL, SwitchReason.POLICY
        )
    with pytest.raises(NotAuthorized):
        await managers.models.purge_tenant(viewer)


async def test_the_sweep_purges_no_living_tenants_fill_sets(managers: Managers) -> None:
    ctx, _ = await managers.tenancy.bootstrap(
        RequestContext(request_id=new_id(), app=APP),
        "Ajax",
        f"ajax-{new_id().hex[-8:]}",
        f"a-{new_id().hex[-8:]}@x.test",
        "Ann",
    )
    session_id = new_id()
    first = await managers.models.resolve_fill_set(ctx, session_id, [MAIN], Eligibility())
    assert await managers.models.purge_tenant(ctx) == 0
    assert await managers.models.get_fill_set(ctx, session_id) == first
