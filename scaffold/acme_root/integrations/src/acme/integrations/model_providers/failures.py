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
    it is never a complete reply. `authentication` says the provider did
    not authenticate the call's key at all, by its own error type; a 401
    says so too. The message is the provider's, for the log, and never
    carries a key. `credential` names the key the call went out on, once the
    engine sets it, and never holds it."""

    code = "model_call_failed"

    def __init__(
        self,
        kind: ErrorKind,
        message: str,
        *,
        status: int | None = None,
        retry_after: float | None = None,
        partial: ModelReply | None = None,
        authentication: bool = False,
    ) -> None:
        super().__init__(f"{kind.value}: {message}")
        self.kind = kind
        self.status = status
        self.retry_after = retry_after
        self.partial = partial
        self.authentication = authentication
        self.credential: str | None = None

    @property
    def key_refused(self) -> bool:
        """Whether the provider refused the key itself: it did not
        authenticate the call, a 401 or its own authentication error. A
        permission the key lacks, a region it refuses, or a model its project
        cannot reach is a credential error of the call alone, never of the
        key."""
        return self.kind is ErrorKind.CREDENTIAL and (self.authentication or self.status == 401)
