"""The provider of a process that calls no model: every call fails as
`credential`, naming why, so a session that reaches it parks with the
reason rather than running on anything."""

from collections.abc import AsyncIterator

from pydantic import SecretStr

from acme.integrations.model_providers import ModelProviderInterface
from acme.integrations.model_providers.calls import ModelCall, StreamPart
from acme.integrations.model_providers.failures import ModelCallFailed
from acme.integrations.model_providers.types import ErrorKind, ProviderName


class ModelProviderAbsentImpl(ModelProviderInterface):
    def __init__(
        self, provider: ProviderName, reason: str = "no model provider is configured"
    ) -> None:
        self._provider = provider
        self._reason = reason

    @property
    def provider(self) -> ProviderName:
        return self._provider

    async def stream(
        self, call: ModelCall, *, credential: SecretStr | None = None
    ) -> AsyncIterator[StreamPart]:
        raise ModelCallFailed(ErrorKind.CREDENTIAL, f"{self._provider.value}: {self._reason}")
        yield  # a stream, though it never yields

    def describe(self) -> str:
        return f"model provider {self._provider.value}: absent, {self._reason}"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None
