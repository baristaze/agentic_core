"""The resolver over a table: each model role's fill and its fallbacks,
from options a root sets. Every fill it answers, fallbacks included, has a
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
    ModelRole,
    RoleFill,
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


class ResolverOptions(Platform):
    table: tuple[RoleFill, ...] = DEFAULT_TABLE


class ModelResolverTableImpl(ModelResolverInterface):
    def __init__(self, prices: ModelPricesInterface, options: ResolverOptions) -> None:
        self._prices = prices
        self._table = {entry.role: entry for entry in options.table}
        if len(self._table) != len(options.table):
            raise ValueError("the resolver's table names a model role twice")

    def check(self, fill: Fill) -> None:
        if not self._prices.priced(fill.provider, fill.model):
            raise UnpricedModel(f"{fill.name} has no price row")

    async def resolve(
        self, ctx: TenantContext, roles: Sequence[ModelRole], eligibility: Eligibility
    ) -> tuple[RoleFill, ...]:
        resolved: list[RoleFill] = []
        for role in sorted(set(roles)):
            entry = self._table.get(role)
            if entry is None:
                raise UnresolvedRole(f"no fill serves the model role {role}")
            admitted = [
                f for f in (entry.fill, *entry.fallbacks) if eligibility.admits(f.eligibility)
            ]
            if not admitted:
                raise UnresolvedRole(
                    f"no fill of the model role {role} meets the session's eligibility"
                )
            for fill in admitted:
                self.check(fill)
            resolved.append(RoleFill(role=role, fill=admitted[0], fallbacks=tuple(admitted[1:])))
        return tuple(resolved)

    def describe(self) -> str:
        return f"model resolver: the table, {len(self._table)} model roles"
