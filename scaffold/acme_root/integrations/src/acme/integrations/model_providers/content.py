"""The one content shape: a provider-neutral block model that runs through
the engine and across the provider boundary. A step's content is made of
these blocks (`acme.om.steps.types.content`), and each provider adapter
translates them both ways. They live here, beneath the object model,
because the object model imports the integrations and never the reverse
(ADR 1005).

A block is one of text, image, document, thinking, tool use, and tool
result, told apart by its `kind`. An adapter builds blocks from what a
model returns, so every block is validated as it is built: a block of an
unknown kind, a field of the wrong type, or a field no block has is
refused there, and nothing past it reads a loose dictionary. An image or a
document is a reference to an attachment, never its bytes.

Every string a block holds is one both storage impls keep as it is
(`Stored`): a NUL, which Postgres refuses in text and in JSON, and a lone
surrogate, which no UTF-8 encodes, each become U+FFFD when the block is
built, so a step the memory impl keeps is the step Postgres keeps, and
neither append fails on what a model wrote."""

import math
import re
from collections.abc import Mapping
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BeforeValidator, Field

from acme.infra.base import FrozenMapping, InfraModel
from acme.integrations.model_providers.types import ProviderName

MAX_NAME = 200
"""The longest tool name or tool-use id a block carries: a name a model
writes is bounded before it is stored."""

REPLACEMENT = "\ufffd"
LONE_SURROGATE = re.compile(r"[\ud800-\udfff]")
"""A surrogate code point in a Python string is always a lone one: a pair is
decoded into the one character it encodes."""


def storable(text: str) -> str:
    """`text` with each NUL and each lone surrogate replaced by U+FFFD."""
    return LONE_SURROGATE.sub(REPLACEMENT, text.replace("\x00", REPLACEMENT))


def storable_value(value: Any) -> Any:
    """A JSON value made storable all the way down: every string, a key
    included, through `storable`, and a float that is not finite, which
    JSON has no word for, as None."""
    if isinstance(value, str):
        return storable(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Mapping):
        return {storable(str(key)): storable_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [storable_value(item) for item in value]
    return value


def _storable_input(value: Any) -> Any:
    """`storable` ahead of the string's own validation, which refuses a lone
    surrogate outright; anything but a string is left to that validation."""
    return storable(value) if isinstance(value, str) else value


Stored = Annotated[str, BeforeValidator(_storable_input)]
"""A string as both storage impls keep it."""

StoredMapping = Annotated[FrozenMapping, BeforeValidator(storable_value)]
"""A frozen JSON mapping as both storage impls keep it."""


class TextBlock(InfraModel):
    kind: Literal["text"] = "text"
    text: Stored


class ImageBlock(InfraModel):
    """An image, by the id of its attachment."""

    kind: Literal["image"] = "image"
    attachment_id: UUID


class DocumentBlock(InfraModel):
    """A document, by the id of its attachment."""

    kind: Literal["document"] = "document"
    attachment_id: UUID


class ThinkingSource(InfraModel):
    """The provider and the model that thought a block. Thinking replays to
    that model alone."""

    provider: ProviderName
    model: Stored = Field(min_length=1, max_length=MAX_NAME)


class ThinkingBlock(InfraModel):
    """The model's thinking: a child of the response that thought it, never
    its main content. `signature` is the provider's proof of it, opaque to
    the engine, and a block replays only with it, to the model that thought
    it. A `redacted` block holds no readable text, only its signature. `ref`
    is the provider's own id for the block, where its replay needs one."""

    kind: Literal["thinking"] = "thinking"
    text: Stored = ""
    signature: Stored | None = None
    source: ThinkingSource | None = None
    redacted: bool = False
    ref: Stored | None = Field(default=None, max_length=MAX_NAME)

    def replays_to(self, provider: ProviderName, model: str) -> bool:
        """Whether a request to `model` of `provider` may carry this block."""
        return self.signature is not None and self.source == ThinkingSource(
            provider=provider, model=model
        )


class ToolUseBlock(InfraModel):
    """A model's call of a tool: the id the model gave the call, the tool's
    name, and its input. A `tool_request` step references this block by the
    response that holds it and this id; it never copies it."""

    kind: Literal["tool_use"] = "tool_use"
    id: Stored = Field(min_length=1, max_length=MAX_NAME)
    name: Stored = Field(min_length=1, max_length=MAX_NAME)
    input: StoredMapping = Field(default_factory=dict, validate_default=True)


ResultPart = Annotated[TextBlock | ImageBlock | DocumentBlock, Field(discriminator="kind")]
"""What a tool's result may hold."""


class ToolResultBlock(InfraModel):
    """What a tool returned for one tool use, or the failure it met."""

    kind: Literal["tool_result"] = "tool_result"
    tool_use_id: Stored = Field(min_length=1, max_length=MAX_NAME)
    parts: tuple[ResultPart, ...] = ()
    is_error: bool = False


Block = Annotated[
    TextBlock | ImageBlock | DocumentBlock | ThinkingBlock | ToolUseBlock | ToolResultBlock,
    Field(discriminator="kind"),
]

ReplyBlock = Annotated[TextBlock | ToolUseBlock, Field(discriminator="kind")]
"""What a response's main content holds; its thinking is a child."""
