"""Model providers: the hosted models the engine calls. One interface, an
adapter per provider (`anthropic.py`, `openai.py`), the scripted provider
that stands in for every one of them (`scripted.py`), and the absent
provider of a process that calls none (`absent.py`).

An adapter translates the one content shape both ways and names what does
not survive (`Dropped`): thinking replays only to the model that thought
it, with its signature, and a cache marker means nothing to a provider
that caches on its own. It reads a provider's error from its status and
its message into a kind (`ErrorKind`). It retries nothing: the engine owns
the policy and could not see what an adapter swallowed.

The client for a call is chosen per call, by provider and credential: the
registry answers the provider's adapter, and a call may carry its own
credential, such as a tenant's key, in place of the platform's."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from pydantic import SecretStr

from acme.integrations.model_providers.calls import Finished, ModelCall, ModelReply, StreamPart
from acme.integrations.model_providers.failures import ModelCallFailed
from acme.integrations.model_providers.types import ErrorKind, ProviderName


class ModelProviderInterface(ABC):
    @property
    @abstractmethod
    def provider(self) -> ProviderName:
        """The provider this adapter speaks for."""
        ...

    @abstractmethod
    def stream(
        self, call: ModelCall, *, credential: SecretStr | None = None
    ) -> AsyncIterator[StreamPart]:
        """The call's response as it arrives, ending in `Finished` with the
        reply, whole. `credential` is the key this call runs on; None runs it
        on the platform's, and with neither the call fails as `credential`.
        A failure is `ModelCallFailed` with its kind; a stream that breaks
        after it began carries what arrived as its `partial`, truncated."""
        ...

    @abstractmethod
    def describe(self) -> str: ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...


class ModelProvidersInterface(ABC):
    """Every provider's adapter, one per provider, chosen per call."""

    @abstractmethod
    def get(self, provider: ProviderName) -> ModelProviderInterface: ...

    @abstractmethod
    def describe(self) -> list[str]: ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...


async def reply_of(parts: AsyncIterator[StreamPart]) -> ModelReply:
    """Drains a stream to its reply. A stream that ends with none is a
    broken one, `transient`."""
    async for part in parts:
        if isinstance(part, Finished):
            return part.reply
    raise ModelCallFailed(ErrorKind.TRANSIENT, "the stream ended with no reply")
