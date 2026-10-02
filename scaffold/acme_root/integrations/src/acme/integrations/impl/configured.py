"""The integrations root that picks each provider from settings, and the
root over providers a caller already built. A twin is refused in a
deployed environment, the way the infra root refuses a local backend; the
caller says whether the environment is one, from the settings it booted
with."""

from datetime import timedelta

import httpx

from acme.integrations.exceptions import UnsafeIntegration
from acme.integrations.identity import IdentityProviderInterface
from acme.integrations.identity.absent import IdentityProviderAbsentImpl
from acme.integrations.identity.twin import IdentityProviderTwinImpl
from acme.integrations.identity.workos import IdentityProviderWorkOSImpl
from acme.integrations.model_providers import ModelProvidersInterface
from acme.integrations.model_providers.anthropic import ModelProviderAnthropicImpl
from acme.integrations.model_providers.openai import ModelProviderOpenAIImpl
from acme.integrations.model_providers.registry import (
    ModelProvidersOverImpl,
    absent_model_providers,
    scripted_model_providers,
)
from acme.integrations.model_providers.types import ProviderName
from acme.integrations.root import IntegrationsInterface
from acme.integrations.settings import IntegrationsSettings


def refuse_unsafe(settings: IntegrationsSettings, environment: str, deployed: bool) -> None:
    """A twin in a deployed environment is refused at boot, naming the setting."""
    if deployed and settings.identity_provider == "twin":
        raise UnsafeIntegration(
            f"ACME_IDENTITY_PROVIDER=twin is refused when ACME_ENVIRONMENT={environment}"
        )
    if deployed and settings.model_providers == "scripted":
        raise UnsafeIntegration(
            f"ACME_MODEL_PROVIDERS=scripted is refused when ACME_ENVIRONMENT={environment}"
        )


def model_providers_for(settings: IntegrationsSettings) -> ModelProvidersInterface:
    """Each provider's adapter, from settings. A live adapter with no platform
    key still serves a call that carries its own credential."""
    if settings.model_providers == "scripted":
        return scripted_model_providers()
    if settings.model_providers == "live":
        timeout = timedelta(seconds=settings.model_timeout_seconds)
        return ModelProvidersOverImpl(
            {
                ProviderName.ANTHROPIC: ModelProviderAnthropicImpl(
                    http=httpx.AsyncClient(),
                    api_key=settings.anthropic_api_key,
                    base_url=settings.anthropic_base_url,
                    timeout=timeout,
                ),
                ProviderName.OPENAI: ModelProviderOpenAIImpl(
                    http=httpx.AsyncClient(),
                    api_key=settings.openai_api_key,
                    base_url=settings.openai_base_url,
                    timeout=timeout,
                ),
            }
        )
    return absent_model_providers()


def identity_provider_for(settings: IntegrationsSettings) -> IdentityProviderInterface:
    if settings.identity_provider == "twin":
        return IdentityProviderTwinImpl()
    if settings.identity_provider == "workos":
        if not settings.workos_client_id:
            return IdentityProviderAbsentImpl("ACME_WORKOS_CLIENT_ID is not set")
        if settings.workos_api_key is None:
            return IdentityProviderAbsentImpl("ACME_WORKOS_API_KEY is not set")
        secret = settings.workos_webhook_secret
        return IdentityProviderWorkOSImpl(
            client_id=settings.workos_client_id,
            api_key=settings.workos_api_key.get_secret_value(),
            base_url=settings.workos_base_url,
            timeout=timedelta(seconds=settings.workos_timeout_seconds),
            webhook_secret=None if secret is None else secret.get_secret_value(),
        )
    return IdentityProviderAbsentImpl()


class IntegrationsOverImpl(IntegrationsInterface):
    """The root over providers already built: a test's twins, or the absent
    providers of a process that signs nobody in and calls no model."""

    def __init__(
        self, identity: IdentityProviderInterface, model_providers: ModelProvidersInterface
    ) -> None:
        self._identity = identity
        self._model_providers = model_providers

    def get_identity_provider(self) -> IdentityProviderInterface:
        return self._identity

    def get_model_providers(self) -> ModelProvidersInterface:
        return self._model_providers

    def describe(self) -> list[str]:
        return [self._identity.describe(), *self._model_providers.describe()]

    async def start(self) -> None:
        await self._identity.start()
        await self._model_providers.start()

    async def close(self) -> None:
        await self._identity.close()
        await self._model_providers.close()


def absent_integrations() -> IntegrationsInterface:
    """The root of a process that signs nobody in and calls no model."""
    return IntegrationsOverImpl(IdentityProviderAbsentImpl(), absent_model_providers())


class IntegrationsConfiguredImpl(IntegrationsOverImpl):
    def __init__(self, settings: IntegrationsSettings, environment: str, deployed: bool) -> None:
        refuse_unsafe(settings, environment, deployed)
        super().__init__(identity_provider_for(settings), model_providers_for(settings))
