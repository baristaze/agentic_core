"""What a model call raises when the provider does not answer it whole."""

from acme.infra.exceptions import InfraException
from acme.integrations.model_providers.calls import ModelReply
from acme.integrations.model_providers.types import ErrorKind


class ModelCallFailed(InfraException):
    """A model call the provider did not answer whole. `kind` is read from
    the provider's status and its message, and decides what the engine does
    (`ErrorKind.answer`). `retry_after` is the provider's own wait, in
    seconds, which no retry comes sooner than. `partial` holds what a broken
    stream delivered before it broke, truncated, for the engine to record;
    it is never a complete reply. The message is the provider's, for the
    log, and never carries a key."""

    code = "model_call_failed"

    def __init__(
        self,
        kind: ErrorKind,
        message: str,
        *,
        status: int | None = None,
        retry_after: float | None = None,
        partial: ModelReply | None = None,
    ) -> None:
        super().__init__(f"{kind.value}: {message}")
        self.kind = kind
        self.status = status
        self.retry_after = retry_after
        self.partial = partial
