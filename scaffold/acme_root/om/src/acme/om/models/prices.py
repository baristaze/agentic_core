"""What the resolver asks of prices: whether a model has a row of its own
in the one source of prices, never a default. The source is the budgets'
pricing; this narrow interface is all the resolver reads of it, so a root
wires the one to the other."""

from abc import ABC, abstractmethod

from acme.integrations.model_providers.types import ProviderName


class ModelPricesInterface(ABC):
    @abstractmethod
    def priced(self, provider: ProviderName, model: str) -> bool:
        """Whether `model` of `provider` has a price row of its own."""
        ...

    @abstractmethod
    def describe(self) -> str: ...
