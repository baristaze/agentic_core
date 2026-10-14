"""The models swimlane over the memory storage: a session resolves its fills
once, a switch is a new version announced by a `switched` step naming both
fills, and nothing a resolver can pick lacks a price row."""

from pathlib import Path
from uuid import UUID

import pytest
from contracts.agent_step_storage import make_message
from contracts.doubles import APP, context
from contracts.fill_set_storage import HAIKU, SOL, SONNET

from acme.infra.impl.local import InfraLocalImpl
from acme.integrations.model_providers.types import Effort, ProviderName
from acme.om.agent_steps.types.agent_step import AgentStep, AgentStepType
from acme.om.agent_steps.types.header import SwitchedHeader
from acme.om.base import new_id
from acme.om.budgets.impl.pricing import PricingTableImpl
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
from acme.om.models.impl.prices import ModelPricesFromPricingImpl, ModelPricesNullImpl
from acme.om.models.impl.resolver import (
    DEFAULT_SIZES,
    DEFAULT_TABLE,
    ModelResolverTableImpl,
    ResolverOptions,
)
from acme.om.models.prices import ModelPricesInterface
from acme.om.models.storage.impl.memory import FillSetStorageMemoryImpl
from acme.om.models.types.fill import (
    MAIN,
    SUMMARIZER,
    Eligibility,
    Fill,
    FillSet,
    ModelAsk,
    ModelSize,
    RoleFill,
    SwitchReason,
)
from acme.om.root import Managers, build_managers
from acme.om.storage.impl.memory import StorageMemoryImpl

UNLISTED = Fill(
    provider=ProviderName.ANTHROPIC,
    model="claude-unlisted",
    max_output_tokens=1_000,
    context_window=100_000,
)
REGIONAL = SOL.model_copy(update={"eligibility": Eligibility(region="eu", zero_retention=True)})
TABLE = (
    RoleFill(role=MAIN, fill=SONNET, fallbacks=(SOL, REGIONAL)),
    RoleFill(role=SUMMARIZER, fill=HAIKU),
)
DEFAULT_MAIN = next(entry for entry in DEFAULT_TABLE if entry.role == MAIN)


class Priced(ModelPricesInterface):
    """Prices that hold a row for each fill named, and for nothing else."""

    def __init__(self, *fills: Fill) -> None:
        self._rows = {(f.provider, f.model) for f in fills}

    def priced(self, provider: ProviderName, model: str) -> bool:
        return (provider, model) in self._rows

    def describe(self) -> str:
        return "model prices: the test's rows"


EVERY_DEFAULT = Priced(
    *(f for entry in (*DEFAULT_TABLE, *DEFAULT_SIZES) for f in (entry.fill, *entry.fallbacks))
)


@pytest.fixture
def managers(tmp_path: Path) -> Managers:
    return build_managers(StorageMemoryImpl(), InfraLocalImpl(tmp_path), model_prices=EVERY_DEFAULT)


async def a_run(managers: Managers, ctx: TenantContext) -> tuple[UUID, int, UUID]:
    """A session with a loop open and a run holding it: its id, the run's
    epoch, and the loop's id."""
    agent_session_id = new_id()
    (first,) = await managers.agent_steps.append_inputs(
        ctx, agent_session_id, [make_message(agent_session_id)]
    )
    epoch = await managers.agent_steps.begin_run(ctx, agent_session_id)
    return agent_session_id, epoch, first.id


async def switches(
    managers: Managers, ctx: TenantContext, agent_session_id: UUID
) -> list[AgentStep]:
    page = await managers.agent_steps.get_agent_steps(ctx, agent_session_id, 0, 100)
    return [s for s in page.items if s.type is AgentStepType.SWITCHED]


# The resolver and its one source of prices.


async def test_the_resolver_refuses_a_model_with_no_price_row() -> None:
    ctx = context(Role.MEMBER)
    prices = Priced(SONNET, SOL, HAIKU)
    for table in (
        (RoleFill(role=MAIN, fill=UNLISTED),),
        (RoleFill(role=MAIN, fill=SONNET, fallbacks=(UNLISTED,)),),
    ):
        resolver = ModelResolverTableImpl(prices, ResolverOptions(table=table))
        with pytest.raises(UnpricedModel):
            await resolver.resolve(ctx, [MAIN], Eligibility())
    resolver = ModelResolverTableImpl(prices, ResolverOptions(table=TABLE))
    with pytest.raises(UnpricedModel):
        resolver.check(UNLISTED)
    with pytest.raises(ValueError, match="twice"):
        ModelResolverTableImpl(prices, ResolverOptions(table=(*TABLE, TABLE[0])))


async def test_a_root_resolves_only_what_the_list_table_prices(tmp_path: Path) -> None:
    rooted = build_managers(StorageMemoryImpl(), InfraLocalImpl(tmp_path))
    ctx = context(Role.MEMBER)
    fills = await rooted.models.resolve_fill_set(ctx, new_id(), [MAIN, SUMMARIZER], Eligibility())
    assert [r.role for r in fills.roles] == [MAIN, SUMMARIZER], "the default table, all priced"
    unlisted_fill = SONNET.model_copy(update={"model": "a-model-the-list-does-not-hold"})
    unlisted = ModelResolverTableImpl(
        ModelPricesFromPricingImpl(PricingTableImpl()),
        ResolverOptions(table=(RoleFill(role=MAIN, fill=unlisted_fill),)),
    )
    with pytest.raises(UnpricedModel):
        await unlisted.resolve(ctx, [MAIN], Eligibility())
    resolver = ModelResolverTableImpl(ModelPricesNullImpl(), ResolverOptions())
    with pytest.raises(UnpricedModel):
        await resolver.resolve(ctx, [SUMMARIZER], Eligibility())


async def test_a_role_resolves_within_the_sessions_eligibility() -> None:
    resolver = ModelResolverTableImpl(Priced(SONNET, SOL, HAIKU), ResolverOptions(table=TABLE))
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


# A kind's asks: a model size and an effort for a role, never a model.


def served(role: RoleFill) -> list[tuple[str, Effort | None]]:
    """Each fill of a role, its fallbacks after it: the model and its effort."""
    return [(f.name, f.effort) for f in (role.fill, *role.fallbacks)]


async def test_a_kind_that_asks_sizes_gets_the_tables_fills_at_its_efforts() -> None:
    resolver = ModelResolverTableImpl(
        ModelPricesFromPricingImpl(PricingTableImpl()), ResolverOptions()
    )
    ctx = context(Role.MEMBER)
    asks = (
        ModelAsk(role=MAIN, size=ModelSize.L, effort=Effort.HIGH),
        ModelAsk(role=SUMMARIZER, size=ModelSize.S, effort=Effort.LOW),
        ModelAsk(role="analysis", size=ModelSize.XL, effort=Effort.MEDIUM),
    )
    analysis, main, summarizer = await resolver.resolve(
        ctx, [MAIN, SUMMARIZER, "analysis"], Eligibility(), asks
    )
    sizes = {entry.size: entry for entry in DEFAULT_SIZES}
    for role, size in ((main, ModelSize.L), (summarizer, ModelSize.S), (analysis, ModelSize.XL)):
        row = sizes[size]
        assert [f.name for f in (role.fill, *role.fallbacks)] == [
            f.name for f in (row.fill, *row.fallbacks)
        ], f"{role.role} runs on the models its size names"
    assert served(main) == [
        ("anthropic/claude-sonnet-5-5", Effort.HIGH),
        ("openai/gpt-6.1-sol", Effort.HIGH),
    ]
    assert served(summarizer) == [
        ("anthropic/claude-haiku-4-5", None),
        ("openai/gpt-6-luna", Effort.LOW),
    ], "a model that takes no effort runs at its own"
    assert served(analysis) == [
        ("anthropic/claude-opus-5-5", Effort.MEDIUM),
        ("openai/gpt-6-astra", Effort.MEDIUM),
    ], "a role no table names, served by its size, at the effort asked"
    assert (main.ask, summarizer.ask, analysis.ask) == (asks[0], asks[1], asks[2])
    # A kind with no ask resolves as before; one role asked leaves the other.
    assert await resolver.resolve(ctx, [MAIN, SUMMARIZER], Eligibility()) == DEFAULT_TABLE
    raised = ModelAsk(role=MAIN, size=ModelSize.L, effort=Effort.XHIGH)
    main, summarizer = await resolver.resolve(ctx, [MAIN, SUMMARIZER], Eligibility(), (raised,))
    assert served(main) == [
        ("anthropic/claude-sonnet-5-5", Effort.XHIGH),
        ("openai/gpt-6.1-sol", Effort.XHIGH),
    ]
    assert summarizer == DEFAULT_TABLE[1]
    with pytest.raises(UnresolvedRole):
        await resolver.resolve(ctx, [MAIN], Eligibility(), (raised, raised))
    unsized = ModelResolverTableImpl(EVERY_DEFAULT, ResolverOptions(sizes=()))
    with pytest.raises(UnresolvedRole, match="model size"):
        await unsized.resolve(ctx, [MAIN], Eligibility(), (raised,))


async def test_an_ask_never_widens_what_a_fill_allows_and_the_fill_set_keeps_both(
    managers: Managers,
) -> None:
    ctx = context(Role.MEMBER)
    agent_session_id = new_id()
    asks = (
        ModelAsk(role=MAIN, size=ModelSize.L, effort=Effort.NONE),
        ModelAsk(role=SUMMARIZER, size=ModelSize.S, effort=Effort.HIGH),
    )
    await managers.models.resolve_fill_set(
        ctx, agent_session_id, [MAIN, SUMMARIZER], Eligibility(), asks
    )
    main, summarizer = (await managers.models.get_fill_set(ctx, agent_session_id)).roles
    assert served(main) == [
        ("anthropic/claude-sonnet-5-5", Effort.HIGH),
        ("openai/gpt-6.1-sol", Effort.HIGH),
    ], "neither model takes none, and each keeps its own"
    assert served(summarizer) == [
        ("anthropic/claude-haiku-4-5", None),
        ("openai/gpt-6-luna", Effort.HIGH),
    ], "the haiku takes no effort, and keeps its own"
    assert (main.ask, summarizer.ask) == asks, "the fill set keeps the ask beside what it got"
    assert Effort.NONE not in main.fallbacks[0].efforts


async def test_each_openai_fill_lists_exactly_the_efforts_its_model_takes() -> None:
    low_to_max = (Effort.LOW, Effort.MEDIUM, Effort.HIGH, Effort.XHIGH, Effort.MAX)
    takes = {
        "gpt-6.1-sol": low_to_max,
        "gpt-6-astra": low_to_max,
        "gpt-6-luna": (Effort.NONE, *low_to_max),
    }
    openai = [
        f
        for entry in DEFAULT_SIZES
        for f in (entry.fill, *entry.fallbacks)
        if f.provider is ProviderName.OPENAI
    ]
    assert {f.model for f in openai} == set(takes)
    for f in openai:
        assert f.efforts == takes[f.model], f"{f.model} lists the levels its provider lists"
    resolver = ModelResolverTableImpl(
        ModelPricesFromPricingImpl(PricingTableImpl()), ResolverOptions()
    )
    ctx = context(Role.MEMBER)
    for asked, fallback in ((Effort.NONE, Effort.HIGH), (Effort.MAX, Effort.MAX)):
        ask = ModelAsk(role=MAIN, size=ModelSize.L, effort=asked)
        (main,) = await resolver.resolve(ctx, [MAIN], Eligibility(), (ask,))
        assert served(main)[1] == ("openai/gpt-6.1-sol", fallback), f"an ask at {asked}"


# A session's fill set, and its switches.


async def test_a_session_resolves_its_fills_once(managers: Managers) -> None:
    ctx = context(Role.MEMBER)
    agent_session_id = new_id()
    with pytest.raises(NotFound):
        await managers.models.get_fill_set(ctx, agent_session_id)
    first = await managers.models.resolve_fill_set(
        ctx, agent_session_id, [MAIN, SUMMARIZER], Eligibility()
    )
    assert first.version == 1 and first.reason is None and first.switched_by is None
    assert first.fill_for(MAIN) == DEFAULT_MAIN.fill
    again = await managers.models.resolve_fill_set(
        ctx, agent_session_id, [MAIN], Eligibility(region="eu")
    )
    assert again == first, "a later resolution keeps the first"
    assert await managers.models.get_fill_set(ctx, agent_session_id) == first
    with pytest.raises(NotFound):
        await managers.models.get_fill_set(context(Role.MEMBER), agent_session_id)


async def test_a_switch_writes_a_new_version_and_a_switched_step_naming_both_fills(
    managers: Managers,
) -> None:
    ctx = context(Role.MEMBER)
    agent_session_id, epoch, loop_id = await a_run(managers, ctx)
    first = await managers.models.resolve_fill_set(
        ctx, agent_session_id, [MAIN, SUMMARIZER], Eligibility()
    )
    to = DEFAULT_MAIN.fallbacks[0]
    second = await managers.models.switch_fill(
        ctx, agent_session_id, epoch, loop_id, MAIN, to, SwitchReason.FALLBACK
    )
    (step,) = await switches(managers, ctx, agent_session_id)
    assert isinstance(step.header, SwitchedHeader)
    named = step.header.fills
    assert named is not None
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
    assert await managers.models.get_fill_set(ctx, agent_session_id) == second


async def test_a_run_that_lost_its_claim_switches_nothing(managers: Managers) -> None:
    ctx = context(Role.MEMBER)
    agent_session_id, stale, loop_id = await a_run(managers, ctx)
    first = await managers.models.resolve_fill_set(ctx, agent_session_id, [MAIN], Eligibility())
    await managers.agent_steps.begin_run(ctx, agent_session_id)
    with pytest.raises(StaleWriter):
        await managers.models.switch_fill(
            ctx, agent_session_id, stale, loop_id, MAIN, SOL, SwitchReason.POLICY
        )
    assert await switches(managers, ctx, agent_session_id) == []
    assert await managers.models.get_fill_set(ctx, agent_session_id) == first


@pytest.mark.parametrize(
    "role,to,refused",
    [
        (MAIN, DEFAULT_MAIN.fill, ValidationFailed),  # the fill it holds
        ("title", SOL, ValidationFailed),  # a role the set does not hold
        (MAIN, UNLISTED, UnpricedModel),
    ],
)
async def test_a_refused_switch_writes_nothing(
    managers: Managers, role: str, to: Fill, refused: type[Exception]
) -> None:
    ctx = context(Role.MEMBER)
    agent_session_id, epoch, loop_id = await a_run(managers, ctx)
    first = await managers.models.resolve_fill_set(ctx, agent_session_id, [MAIN], Eligibility())
    with pytest.raises(refused):
        await managers.models.switch_fill(
            ctx, agent_session_id, epoch, loop_id, role, to, SwitchReason.POLICY
        )
    assert await switches(managers, ctx, agent_session_id) == []
    assert await managers.models.get_fill_set(ctx, agent_session_id) == first


async def test_a_switch_stays_inside_the_sessions_eligibility(managers: Managers) -> None:
    ctx = context(Role.MEMBER)
    agent_session_id, epoch, loop_id = await a_run(managers, ctx)
    ineligible = SOL.model_copy(update={"eligibility": Eligibility(region="us")})
    eligible = SOL.model_copy(update={"eligibility": Eligibility(region="eu")})
    lighter = eligible.model_copy(update={"effort": Effort.LOW})
    models = ModelsManagerImpl(
        FillSetStorageMemoryImpl(),
        managers.agent_steps,
        managers.tenancy,
        ModelResolverTableImpl(
            Priced(eligible, ineligible, lighter),
            ResolverOptions(
                table=(RoleFill(role=MAIN, fill=eligible, fallbacks=(ineligible, lighter)),)
            ),
        ),
        ModelsOptions(),
    )
    await models.resolve_fill_set(ctx, agent_session_id, [MAIN], Eligibility(region="eu"))
    with pytest.raises(ValidationFailed):
        await models.switch_fill(
            ctx, agent_session_id, epoch, loop_id, MAIN, ineligible, SwitchReason.POLICY
        )
    fallen = await models.fall_back(ctx, agent_session_id, epoch, loop_id, MAIN)
    assert fallen is not None and fallen.fill_for(MAIN) == lighter, "the ineligible one is passed"
    assert await models.fall_back(ctx, agent_session_id, epoch, loop_id, MAIN) is None, (
        "none is left"
    )
    assert len(await switches(managers, ctx, agent_session_id)) == 1


async def test_a_fallback_takes_the_next_declared_fill_it_has_not_tried(managers: Managers) -> None:
    ctx = context(Role.MEMBER)
    agent_session_id, epoch, loop_id = await a_run(managers, ctx)
    head = await managers.models.resolve_fill_set(ctx, agent_session_id, [MAIN], Eligibility())
    declared = DEFAULT_MAIN.fallbacks
    assert rules.next_fallback(head, MAIN) == declared[0]
    assert rules.next_fallback(head, MAIN, tried=declared) is None
    assert rules.next_fallback(head, "title") is None
    fallen = await managers.models.fall_back(ctx, agent_session_id, epoch, loop_id, MAIN)
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
    agent_session_id, epoch, loop_id = await a_run(managers, ctx)
    storage = CrashAfterTheStep()
    models = ModelsManagerImpl(
        storage,
        managers.agent_steps,
        managers.tenancy,
        ModelResolverTableImpl(EVERY_DEFAULT, ResolverOptions()),
        ModelsOptions(),
    )
    await models.resolve_fill_set(ctx, agent_session_id, [MAIN], Eligibility())
    with pytest.raises(ConnectionError):
        await models.switch_fill(
            ctx, agent_session_id, epoch, loop_id, MAIN, SOL, SwitchReason.UPGRADE
        )
    (step,) = await switches(managers, ctx, agent_session_id)
    assert (await models.get_fill_set(ctx, agent_session_id)).version == 1, "the step is ahead"
    assert isinstance(step.header, SwitchedHeader)
    fills, at = step.header.fills, step.created_at
    assert fills is not None
    settled = await models.settle_switch(ctx, agent_session_id, step.id, fills, at)
    assert (settled.version, settled.switched_by, settled.fill_for(MAIN)) == (2, step.id, SOL)
    again = await models.settle_switch(ctx, agent_session_id, step.id, fills, at)
    assert again == settled, "settled twice, written once"
    assert await models.get_fill_set(ctx, agent_session_id) == settled
    with pytest.raises(PreconditionFailed):
        await models.settle_switch(ctx, agent_session_id, new_id(), fills, at)
    later = fills.model_copy(update={"fill_set_version": 4})
    with pytest.raises(PreconditionFailed):
        await models.settle_switch(ctx, agent_session_id, new_id(), later, at)


async def test_a_viewer_reads_and_switches_nothing(managers: Managers) -> None:
    member = context(Role.MEMBER)
    agent_session_id, epoch, loop_id = await a_run(managers, member)
    viewer = context(Role.VIEWER)
    with pytest.raises(NotAuthorized):
        await managers.models.resolve_fill_set(viewer, agent_session_id, [MAIN], Eligibility())
    with pytest.raises(NotAuthorized):
        await managers.models.switch_fill(
            viewer, agent_session_id, epoch, loop_id, MAIN, SOL, SwitchReason.POLICY
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
    agent_session_id = new_id()
    first = await managers.models.resolve_fill_set(ctx, agent_session_id, [MAIN], Eligibility())
    assert await managers.models.purge_tenant(ctx) == 0
    assert await managers.models.get_fill_set(ctx, agent_session_id) == first
