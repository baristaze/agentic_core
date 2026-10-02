"""The resolver over a table: each model role's fill and its fallbacks,
from options a root sets. It checks every fill in the table against the
one source of prices when it is built, so a process with an unpriced model
in its table does not start."""

from collections.abc import Sequence

from acme.integrations.model_providers.types import Effort, ProviderName
from acme.om.base import Platform
from acme.om.budgets.pricing import PricingInterface
from acme.om.context import TenantContext
from acme.om.exceptions import UnpricedModel, UnresolvedRole
from acme.om.models.resolver import ModelResolverInterface
from acme.om.models.types.fill import (
    MAIN,
    SUMMARIZER,
    Eligibility,
    Fill,
    ModelRole,
    RoleFill,
)


class RoleTable(Platform):
    """A model role's fill and the fallbacks it declares, in order."""

    fill: Fill
    fallbacks: tuple[Fill, ...] = ()


DEFAULT_TABLE: dict[ModelRole, RoleTable] = {
    MAIN: RoleTable(
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
    SUMMARIZER: RoleTable(
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
}
"""The engine's two model roles, as a fresh copy resolves them. A product
names its own roles and models in its options; each model needs a price
row in the same change."""


class ResolverOptions(Platform):
    table: dict[ModelRole, RoleTable] = DEFAULT_TABLE


class ModelResolverTableImpl(ModelResolverInterface):
    def __init__(self, pricing: PricingInterface, options: ResolverOptions) -> None:
        self._pricing = pricing
        self._table = dict(options.table)
        for entry in self._table.values():
            for fill in (entry.fill, *entry.fallbacks):
                self.check(fill)

    def check(self, fill: Fill) -> None:
        if self._pricing.price(fill.provider, fill.model) is None:
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
