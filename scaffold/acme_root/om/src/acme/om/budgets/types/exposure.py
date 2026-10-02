"""The shape of a model call, as only the engine knows it before the call:
what the hold must cover at worst.

The prompt's size is the provider's count or a proven upper bound, never a
local estimate, and the type has no third way to say it. The output bound is
the most the request lets the model write. Thinking billed outside that
bound is a figure of its own, and every tool the provider runs itself names
the most calls the request lets it make, so its fees have a bound too."""

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from acme.om.base import FrozenMapping, Platform


class PromptCount(StrEnum):
    """Where a prompt's size comes from."""

    PROVIDER = "provider"  # the provider counted it
    UPPER_BOUND = "upper_bound"  # a bound proven to be at least the provider's count


class PromptSize(Platform):
    tokens: int = Field(ge=0)
    counted_by: PromptCount


class CallShape(Platform):
    prompt: PromptSize
    writes_cache: bool = False  # the request writes the provider's prompt cache
    output_bound: int = Field(ge=1)  # the most output tokens the request allows
    thinking_outside: int = Field(default=0, ge=0)  # thinking billed beyond the output bound
    provider_tools: FrozenMapping = Field(default_factory=dict, validate_default=True)
    """Each tool the provider runs itself, by name, and the most calls the
    request lets it make: `{"web_search": 5}`."""

    @model_validator(mode="after")
    def _each_tool_has_a_bound(self) -> Self:
        for name, calls in self.provider_tools.items():
            if not isinstance(calls, int) or isinstance(calls, bool) or calls < 0:
                raise ValueError(f"provider tool {name} names no bound on its calls")
        return self
