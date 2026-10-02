"""What the resolver asks of prices. The root wires the one source, the
budgets' pricing; the null of a root that wired none gives no model a row,
so the resolver refuses every one, and nothing runs on a price it would
have to guess. Loud, never quiet."""

from acme.integrations.model_providers.types import ProviderName
from acme.om.budgets.pricing import PricingInterface
from acme.om.models.prices import ModelPricesInterface


class ModelPricesNullImpl(ModelPricesInterface):
    def priced(self, provider: ProviderName, model: str) -> bool:
        return False

    def describe(self) -> str:
        return "model prices: none wired, so every model is refused"


class ModelPricesFromPricingImpl(ModelPricesInterface):
    """The resolver's question asked of the one source of prices, the
    budgets' pricing: a model is priced when the source holds a row of its
    own for it, never a default row's."""

    def __init__(self, pricing: PricingInterface) -> None:
        self._pricing = pricing

    def priced(self, provider: ProviderName, model: str) -> bool:
        return self._pricing.price_of(provider.value, model) is not None

    def describe(self) -> str:
        return "model prices: the budgets' pricing"
