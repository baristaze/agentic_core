"""The resolver over two tables: each model role's fill and its fallbacks,
and each model size's, from options a root sets. A role a kind asks a size
of resolves by its size, at the ask's effort where a fill allows it; any
other role by its name. Every fill it answers, fallbacks included, has a
price row of its own: it asks before it answers."""

from collections.abc import Sequence

from acme.integrations.model_providers.types import Effort, ProviderName
from acme.om.base import Platform
from acme.om.context import TenantContext
from acme.om.exceptions import UnpricedModel, UnresolvedRole
from acme.om.models.prices import ModelPricesInterface
from acme.om.models.resolver import ModelResolverInterface
from acme.om.models.types.fill import (
    MAIN,
    SUMMARIZER,
    Eligibility,
    Fill,
    ModelAsk,
    ModelRole,
    ModelSize,
    RoleFill,
    SizeFill,
)

DEFAULT_TABLE: tuple[RoleFill, ...] = (
    RoleFill(
        role=MAIN,
        fill=Fill(
            provider=ProviderName.ANTHROPIC,
            model="claude-sonnet-5-5",
            effort=Effort.HIGH,
            max_output_tokens=32_000,
            context_window=1_000_000,
        ),
        fallbacks=(
            Fill(
                provider=ProviderName.OPENAI,
                model="gpt-6.1-sol",
                effort=Effort.HIGH,
                max_output_tokens=32_000,
                context_window=1_050_000,
            ),
        ),
    ),
    RoleFill(
        role=SUMMARIZER,
        fill=Fill(
            provider=ProviderName.ANTHROPIC,
            model="claude-haiku-4-5",
            max_output_tokens=8_000,
            context_window=200_000,
        ),
        fallbacks=(
            Fill(
                provider=ProviderName.OPENAI,
                model="gpt-6-luna",
                effort=Effort.LOW,
                max_output_tokens=8_000,
                context_window=1_050_000,
            ),
        ),
    ),
)
"""The engine's two model roles, as a fresh copy resolves them: each role's
fill and the fallbacks it declares, in order. A product names its own
roles and models in its options; each model needs a price row of its
own."""


ANTHROPIC_EFFORTS = (Effort.LOW, Effort.MEDIUM, Effort.HIGH, Effort.XHIGH, Effort.MAX)
OPENAI_EFFORTS = (Effort.NONE, Effort.LOW, Effort.MEDIUM, Effort.HIGH, Effort.XHIGH)
"""The levels the sizes' models of each provider take. A model that takes
none, such as `claude-haiku-4-5`, names none, and runs at its own."""


DEFAULT_SIZES: tuple[SizeFill, ...] = (
    SizeFill(
        size=ModelSize.XS,
        fill=Fill(
            provider=ProviderName.ANTHROPIC,
            model="claude-haiku-4-5",
            max_output_tokens=4_000,
            context_window=200_000,
        ),
        fallbacks=(
            Fill(
                provider=ProviderName.OPENAI,
                model="gpt-6-luna",
                effort=Effort.NONE,
                efforts=OPENAI_EFFORTS,
                max_output_tokens=4_000,
                context_window=1_050_000,
            ),
        ),
    ),
    SizeFill(
        size=ModelSize.S,
        fill=Fill(
            provider=ProviderName.ANTHROPIC,
            model="claude-haiku-4-5",
            max_output_tokens=8_000,
            context_window=200_000,
        ),
        fallbacks=(
            Fill(
                provider=ProviderName.OPENAI,
                model="gpt-6-luna",
                effort=Effort.LOW,
                efforts=OPENAI_EFFORTS,
                max_output_tokens=8_000,
                context_window=1_050_000,
            ),
        ),
    ),
    SizeFill(
        size=ModelSize.M,
        fill=Fill(
            provider=ProviderName.ANTHROPIC,
            model="claude-sonnet-5-5",
            effort=Effort.MEDIUM,
            efforts=ANTHROPIC_EFFORTS,
            max_output_tokens=16_000,
            context_window=1_000_000,
        ),
        fallbacks=(
            Fill(
                provider=ProviderName.OPENAI,
                model="gpt-6.1-sol",
                effort=Effort.MEDIUM,
                efforts=OPENAI_EFFORTS,
                max_output_tokens=16_000,
                context_window=1_050_000,
            ),
        ),
    ),
    SizeFill(
        size=ModelSize.L,
        fill=Fill(
            provider=ProviderName.ANTHROPIC,
            model="claude-sonnet-5-5",
            effort=Effort.HIGH,
            efforts=ANTHROPIC_EFFORTS,
            max_output_tokens=32_000,
            context_window=1_000_000,
        ),
        fallbacks=(
            Fill(
                provider=ProviderName.OPENAI,
                model="gpt-6.1-sol",
                effort=Effort.HIGH,
                efforts=OPENAI_EFFORTS,
                max_output_tokens=32_000,
                context_window=1_050_000,
            ),
        ),
    ),
    SizeFill(
        size=ModelSize.XL,
        fill=Fill(
            provider=ProviderName.ANTHROPIC,
            model="claude-opus-5-5",
            effort=Effort.HIGH,
            efforts=ANTHROPIC_EFFORTS,
            max_output_tokens=64_000,
            context_window=1_000_000,
        ),
        fallbacks=(
            Fill(
                provider=ProviderName.OPENAI,
                model="gpt-6-astra",
                effort=Effort.HIGH,
                efforts=OPENAI_EFFORTS,
                max_output_tokens=64_000,
                context_window=1_050_000,
            ),
        ),
    ),
)
"""Each model size as a fresh copy resolves it: the fill and the fallbacks
it declares, in order. S serves what the summarizer runs on and L what the
main role runs on, so a kind that asks those sizes gets the models a kind
that asks nothing gets. A product maps the sizes to its own models in its
options; each model needs a price row of its own."""


class ResolverOptions(Platform):
    table: tuple[RoleFill, ...] = DEFAULT_TABLE
    sizes: tuple[SizeFill, ...] = DEFAULT_SIZES


class ModelResolverTableImpl(ModelResolverInterface):
    def __init__(self, prices: ModelPricesInterface, options: ResolverOptions) -> None:
        self._prices = prices
        self._table = {entry.role: entry for entry in options.table}
        if len(self._table) != len(options.table):
            raise ValueError("the resolver's table names a model role twice")
        self._sizes = {entry.size: entry for entry in options.sizes}
        if len(self._sizes) != len(options.sizes):
            raise ValueError("the resolver's table names a model size twice")

    def check(self, fill: Fill) -> None:
        if not self._prices.priced(fill.provider, fill.model):
            raise UnpricedModel(f"{fill.name} has no price row")

    async def resolve(
        self,
        ctx: TenantContext,
        roles: Sequence[ModelRole],
        eligibility: Eligibility,
        asks: Sequence[ModelAsk] = (),
    ) -> tuple[RoleFill, ...]:
        asked = {ask.role: ask for ask in asks}
        if len(asked) != len(asks):
            raise UnresolvedRole("a kind asks a model role twice")
        resolved: list[RoleFill] = []
        for role in sorted(set(roles)):
            ask = asked.get(role)
            entry = self._entry(role, ask)
            admitted = [
                f.at(None if ask is None else ask.effort)
                for f in (entry.fill, *entry.fallbacks)
                if eligibility.admits(f.eligibility)
            ]
            if not admitted:
                raise UnresolvedRole(
                    f"no fill of the model role {role} meets the session's eligibility"
                )
            for fill in admitted:
                self.check(fill)
            resolved.append(
                RoleFill(role=role, fill=admitted[0], fallbacks=tuple(admitted[1:]), ask=ask)
            )
        return tuple(resolved)

    def _entry(self, role: ModelRole, ask: ModelAsk | None) -> RoleFill | SizeFill:
        """The row that serves `role`: its size's when the kind asks one,
        its own otherwise."""
        if ask is not None:
            sized = self._sizes.get(ask.size)
            if sized is None:
                raise UnresolvedRole(f"no fill serves the model size {ask.size} {role} asks")
            return sized
        entry = self._table.get(role)
        if entry is None:
            raise UnresolvedRole(f"no fill serves the model role {role}")
        return entry

    def describe(self) -> str:
        return (
            f"model resolver: the table, {len(self._table)} model roles"
            f" and {len(self._sizes)} model sizes"
        )
