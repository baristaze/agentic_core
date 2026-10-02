"""The shape of a model call, as only the engine knows it before the call:
what the hold must cover at worst.

The prompt's size is the provider's count or a proven upper bound, never a
local estimate, and the type has no third way to say it. The output bound is
the most the request lets the model write. Thinking billed outside that
bound is a figure of its own, and every tool the provider runs itself names
the most calls the request lets it make and the most input one call may add, so its fees
and the input it brings have a bound too."""

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from acme.om.base import Platform
from acme.om.budgets.types.budget import MAX_KEY


class PromptCount(StrEnum):
    """Where a prompt's size comes from."""

    PROVIDER = "provider"  # the provider counted it
    UPPER_BOUND = "upper_bound"  # a bound proven to be at least the provider's count


class CacheWrite(StrEnum):
    """Whether a request writes the provider's prompt cache, and which."""

    NONE = "none"
    SHORT = "short"  # the provider's default cache
    LONG = "long"  # a longer-lived cache, which some providers bill higher


class PromptSize(Platform):
    tokens: int = Field(ge=0)
    counted_by: PromptCount


class ProviderTool(Platform):
    """A tool the provider runs itself, as a request enables it: the most
    calls it may make, and the most input tokens one call may add, such as
    a search's results, which the provider bills as input on top of its fee."""

    name: str = Field(min_length=1, max_length=MAX_KEY)
    calls: int = Field(ge=0)
    input_per_call: int = Field(ge=0)


class CallShape(Platform):
    prompt: PromptSize
    cache: CacheWrite = CacheWrite.NONE  # the prompt cache the request writes
    output_bound: int = Field(ge=1)  # the most output tokens the request allows
    thinking_outside: int = Field(default=0, ge=0)  # thinking billed beyond the output bound
    provider_tools: tuple[ProviderTool, ...] = ()

    @model_validator(mode="after")
    def _one_bound_a_tool(self) -> Self:
        names = [tool.name for tool in self.provider_tools]
        if len(set(names)) != len(names):
            raise ValueError("a provider tool has one bound in a call")
        return self

    @property
    def tool_input(self) -> int:
        """The most input tokens the provider's own tools may add."""
        return sum(tool.calls * tool.input_per_call for tool in self.provider_tools)

    @property
    def input_bound(self) -> int:
        """The most input tokens the call may be billed: the prompt and what
        the provider's tools may add to it."""
        return self.prompt.tokens + self.tool_input
