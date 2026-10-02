"""The prices of a root that wired no source: no model has a row, so the
resolver refuses every one, and nothing runs on a price it would have to
guess. Loud, never quiet."""

from acme.integrations.model_providers.types import ProviderName
from acme.om.models.prices import ModelPricesInterface


class ModelPricesNullImpl(ModelPricesInterface):
    def priced(self, provider: ProviderName, model: str) -> bool:
        return False

    def describe(self) -> str:
        return "model prices: none wired, so every model is refused"
