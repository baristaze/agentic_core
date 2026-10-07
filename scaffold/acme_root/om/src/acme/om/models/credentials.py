"""The client a model call runs on, chosen per call by provider and
credential: the platform's own key, or a tenant's. The loop and a
compaction ask before every call, so a tenant's key reaches the call it
pays for, and the gate and the outage signal name the credential the call
carries, never one it might have.

The engine's own answer is the platform's key, always
(`impl/credentials.py`); a platform answers a tenant on its own key with
that key."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from acme.integrations.model_providers import ModelProviderInterface
from acme.integrations.model_providers.types import ProviderName
from acme.om.context import TenantContext

PLATFORM_CREDENTIAL = "platform"
"""The name of the platform's own key, as the outage signal and the gate
read a call's credential."""


@dataclass(frozen=True)
class CallClient:
    """The client one call runs on, and the name of its credential: the
    platform's (`PLATFORM_CREDENTIAL`), or a tenant key's reference. The
    name never holds a key."""

    credential: str
    client: ModelProviderInterface


class CallCredentialsInterface(ABC):
    @abstractmethod
    async def client_for(self, ctx: TenantContext, provider: ProviderName) -> CallClient:
        """The client a call of the tenant to `provider` runs on. A call
        that needs a key the tenant does not hold is `NoCredential`, with
        nothing spent; nothing falls back to the platform's key."""
        ...

    @abstractmethod
    async def refused(self, ctx: TenantContext, provider: ProviderName, credential: str) -> None:
        """The provider did not authenticate `credential` on a call: a 401,
        or its own authentication error, never a permission the key lacks. A
        tenant's key is offered to no call again, so every session that
        needs it waits for a new one; the platform's own is no tenant's to
        refuse."""
        ...
