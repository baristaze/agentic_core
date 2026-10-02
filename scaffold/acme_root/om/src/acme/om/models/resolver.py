"""The resolver: what turns model roles into fills. It is injected, so the
engine never picks a model; a product, a platform, or a tenant's own keys
decide what it answers. Every fill it answers, fallbacks included, has a
row in the one source of prices."""

from abc import ABC, abstractmethod
from collections.abc import Sequence

from acme.om.context import TenantContext
from acme.om.models.types.fill import Eligibility, Fill, ModelRole, RoleFill


class ModelResolverInterface(ABC):
    @abstractmethod
    async def resolve(
        self, ctx: TenantContext, roles: Sequence[ModelRole], eligibility: Eligibility
    ) -> tuple[RoleFill, ...]:
        """Each role's fill and its declared fallbacks, in role order, every
        one admitted by `eligibility`. A role it does not know, or one no
        admitted fill serves, is `UnresolvedRole`; a fill whose model has no
        price row is `UnpricedModel`."""
        ...

    @abstractmethod
    def check(self, fill: Fill) -> None:
        """Refuses a fill whose model has no price row (`UnpricedModel`), so a
        switch never lands where a resolution could not."""
        ...

    @abstractmethod
    def describe(self) -> str: ...
