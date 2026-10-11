"""The resolver: what turns model roles into fills. It is injected, so the
engine never picks a model; a product, a platform, or a tenant's own keys
decide what it answers. A kind's asks reach it with the roles: a model
size and an effort a role needs, never a model, which a resolver may serve
or leave aside. Every fill it answers, fallbacks included, has a row in the
one source of prices (`models.prices`)."""

from abc import ABC, abstractmethod
from collections.abc import Sequence

from acme.om.context import TenantContext
from acme.om.models.types.fill import Eligibility, Fill, ModelAsk, ModelRole, RoleFill


class ModelResolverInterface(ABC):
    @abstractmethod
    async def resolve(
        self,
        ctx: TenantContext,
        roles: Sequence[ModelRole],
        eligibility: Eligibility,
        asks: Sequence[ModelAsk] = (),
    ) -> tuple[RoleFill, ...]:
        """Each role's fill and its declared fallbacks, in role order, every
        one admitted by `eligibility`. `asks` are the kind's, at most one a
        role: a resolver that serves them keeps each beside its role's fill,
        and runs a fill at an ask's effort only when the fill allows it. A
        role it does not know, or one no admitted fill serves, is
        `UnresolvedRole`; a fill whose model has no price row is
        `UnpricedModel`."""
        ...

    @abstractmethod
    def check(self, fill: Fill) -> None:
        """Refuses a fill whose model has no price row (`UnpricedModel`), so a
        switch never lands where a resolution could not."""
        ...

    @abstractmethod
    def describe(self) -> str: ...
