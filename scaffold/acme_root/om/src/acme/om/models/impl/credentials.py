"""The engine's own answer to which client a call runs on: the platform's
key, for every tenant and every call."""

import logging

from acme.integrations.model_providers import ModelProvidersInterface
from acme.integrations.model_providers.types import ProviderName
from acme.om.context import TenantContext
from acme.om.models.credentials import PLATFORM_CREDENTIAL, CallClient, CallCredentialsInterface

log = logging.getLogger(__name__)


class CallCredentialsPlatformImpl(CallCredentialsInterface):
    """Every call on the platform's key, through the providers' registry;
    `credential` is the name its outage signal is kept under."""

    def __init__(
        self, providers: ModelProvidersInterface, credential: str = PLATFORM_CREDENTIAL
    ) -> None:
        self._providers = providers
        self._credential = credential

    async def client_for(self, ctx: TenantContext, provider: ProviderName) -> CallClient:
        return CallClient(credential=self._credential, client=self._providers.get(provider))

    async def refused(self, ctx: TenantContext, provider: ProviderName, credential: str) -> None:
        # The platform's key is fixed by an operator, never by a tenant's
        # session: the session parks on the provider, and nothing else moves.
        log.error("%s refused the %s credential", provider.value, credential)
