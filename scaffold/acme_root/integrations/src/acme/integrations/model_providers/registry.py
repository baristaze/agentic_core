"""The registry over adapters already built: one per provider, every
provider held, so a call names its provider and gets an adapter, never a
missing one."""

from collections.abc import Mapping

from acme.integrations.model_providers import ModelProviderInterface, ModelProvidersInterface
from acme.integrations.model_providers.absent import ModelProviderAbsentImpl
from acme.integrations.model_providers.scripted import ModelProviderScriptedImpl
from acme.integrations.model_providers.types import ProviderName


class ModelProvidersOverImpl(ModelProvidersInterface):
    def __init__(self, providers: Mapping[ProviderName, ModelProviderInterface]) -> None:
        missing = [p.value for p in ProviderName if p not in providers]
        if missing:
            raise ValueError(f"no adapter for {', '.join(missing)}: a root wires one for each")
        crossed = [p.value for p, adapter in providers.items() if adapter.provider is not p]
        if crossed:
            raise ValueError(f"an adapter speaks for another provider than {', '.join(crossed)}")
        self._providers = dict(providers)

    def get(self, provider: ProviderName) -> ModelProviderInterface:
        return self._providers[provider]

    def describe(self) -> list[str]:
        return [self._providers[p].describe() for p in ProviderName]

    async def start(self) -> None:
        for provider in ProviderName:
            await self._providers[provider].start()

    async def close(self) -> None:
        for provider in ProviderName:
            await self._providers[provider].close()


def absent_model_providers(
    reason: str = "no model provider is configured",
) -> ModelProvidersInterface:
    """The registry of a process that calls no model."""
    return ModelProvidersOverImpl({p: ModelProviderAbsentImpl(p, reason) for p in ProviderName})


def scripted_model_providers() -> ModelProvidersInterface:
    """A scripted twin for every provider, each with an empty script."""
    return ModelProvidersOverImpl({p: ModelProviderScriptedImpl(p) for p in ProviderName})
