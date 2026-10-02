"""The names the provider boundary speaks in: which provider, how hard a
model thinks, why a response stopped, what a call used, and what kind of
error a provider's failure is. Every adapter maps its provider's own words
onto these, and nothing past the boundary reads a provider's."""

from enum import StrEnum
from typing import Literal, Self

from pydantic import Field

from acme.infra.base import InfraModel


class ProviderName(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


class Effort(StrEnum):
    """How hard a model works on a call. A provider that has no such level
    for a model leaves it out and names what it dropped."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class StopReason(StrEnum):
    """Why a response stopped. A model's refusal is a response its agent kind
    handles, never a provider error."""

    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    OUTPUT_LIMIT = "output_limit"  # cut by its output bound: never complete
    REFUSAL = "refusal"
    CONTENT_FILTER = "content_filter"
    PAUSE = "pause"  # the provider paused a long turn; the next call continues it


class ErrorAnswer(StrEnum):
    """What the engine does about an error of a kind."""

    RETRY = "retry"  # in process while that is cheaper than parking, then fall back or park
    COMPACT = "compact"  # compact once and retry; a second ends the loop
    RESOLVE = "resolve"  # re-resolve the model role and switch
    PARK = "park"  # never waits: parks at once, naming the unlock
    END = "end"  # ends the loop `errored`, with the evidence


class ErrorKind(StrEnum):
    """A provider error's kind, read from its message as well as its status,
    because providers disagree on codes."""

    TRANSIENT = "transient"
    OVERLOADED = "overloaded"
    RATE_LIMITED = "rate_limited"
    CONTEXT_OVERFLOW = "context_overflow"
    MODEL_UNAVAILABLE = "model_unavailable"
    BILLING = "billing"
    CREDENTIAL = "credential"
    INVALID_REQUEST = "invalid_request"
    PERMANENT = "permanent"

    @property
    def answer(self) -> ErrorAnswer:
        return ANSWERS[self]


ANSWERS: dict[ErrorKind, ErrorAnswer] = {
    ErrorKind.TRANSIENT: ErrorAnswer.RETRY,
    ErrorKind.OVERLOADED: ErrorAnswer.RETRY,
    ErrorKind.RATE_LIMITED: ErrorAnswer.RETRY,
    ErrorKind.CONTEXT_OVERFLOW: ErrorAnswer.COMPACT,
    ErrorKind.MODEL_UNAVAILABLE: ErrorAnswer.RESOLVE,
    ErrorKind.BILLING: ErrorAnswer.PARK,
    ErrorKind.CREDENTIAL: ErrorAnswer.PARK,
    ErrorKind.INVALID_REQUEST: ErrorAnswer.END,
    ErrorKind.PERMANENT: ErrorAnswer.END,
}
"""The spec's table of provider errors: each kind and the engine's answer."""


class Usage(InfraModel):
    """What one call used, in disjoint classes, so no token is counted twice.
    Providers differ on whether cached and reasoning tokens sit inside their
    other counts; each adapter takes them out. Thinking is what the provider
    reports apart from the output, never a count of visible text: a provider
    that bills thinking inside its output count reports it there."""

    input: int = Field(default=0, ge=0)  # prompt tokens neither read from nor written to a cache
    cache_read: int = Field(default=0, ge=0)
    cache_write: int = Field(default=0, ge=0)
    output: int = Field(default=0, ge=0)
    thinking: int = Field(default=0, ge=0)

    @property
    def prompt(self) -> int:
        """Every prompt token, whichever way it was billed."""
        return self.input + self.cache_read + self.cache_write

    def __add__(self, other: Self) -> Self:
        return type(self)(
            input=self.input + other.input,
            cache_read=self.cache_read + other.cache_read,
            cache_write=self.cache_write + other.cache_write,
            output=self.output + other.output,
            thinking=self.thinking + other.thinking,
        )


class Dropped(InfraModel):
    """Something that did not survive translation, named: a request's thinking
    from another provider, a cache marker a provider cannot read, a block of a
    kind the engine does not hold. The engine records it; nothing of it
    crosses the boundary."""

    direction: Literal["request", "response"]
    what: str
    why: str
