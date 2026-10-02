"""What a step says: one provider-neutral block model, and the children that
belong to a step without being its main content.

A block is one of text, image, document, thinking, tool use, and tool
result, told apart by its `kind`. A provider adapter builds blocks from
what a model returns, so every block is validated as it is built: a block
of an unknown kind, a field of the wrong type, or a field no block has is
refused there, and nothing past it reads a loose dictionary. An image or a
document is a reference to an attachment, never its bytes: the step holds
the attachment's placeholder among its children, and the bytes live in
blob storage.

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

from acme.om.base import FrozenMapping, Platform

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


class TextBlock(Platform):
    kind: Literal["text"] = "text"
    text: Stored


class ImageBlock(Platform):
    """An image, by the id of its attachment among the step's children."""

    kind: Literal["image"] = "image"
    attachment_id: UUID


class DocumentBlock(Platform):
    """A document, by the id of its attachment among the step's children."""

    kind: Literal["document"] = "document"
    attachment_id: UUID


class ThinkingBlock(Platform):
    """The model's thinking: a child of the response that thought it, never
    its main content."""

    kind: Literal["thinking"] = "thinking"
    text: Stored


class ToolUseBlock(Platform):
    """A model's call of a tool: the id the model gave the call, the tool's
    name, and its input. A `tool_request` step references this block by the
    response that holds it and this id; it never copies it."""

    kind: Literal["tool_use"] = "tool_use"
    id: Stored = Field(min_length=1, max_length=MAX_NAME)
    name: Stored = Field(min_length=1, max_length=MAX_NAME)
    input: StoredMapping = Field(default_factory=dict, validate_default=True)


ResultPart = Annotated[TextBlock | ImageBlock | DocumentBlock, Field(discriminator="kind")]
"""What a tool's result may hold."""


class ToolResultBlock(Platform):
    """What a tool returned for one tool use, or the failure it met."""

    kind: Literal["tool_result"] = "tool_result"
    tool_use_id: Stored = Field(min_length=1, max_length=MAX_NAME)
    parts: tuple[ResultPart, ...] = ()
    is_error: bool = False


Block = Annotated[
    TextBlock | ImageBlock | DocumentBlock | ThinkingBlock | ToolUseBlock | ToolResultBlock,
    Field(discriminator="kind"),
]


class Content(Platform):
    """A step's main content: its blocks, in order. Plain in memory; sealed
    at rest."""

    blocks: tuple[Block, ...] = ()


class Attachment(Platform):
    """An attachment's placeholder: what the step holds of a file, never its
    bytes. `hash` is keyed by the session, so it confirms nothing once the
    session's key is gone."""

    id: UUID
    name: Stored = Field(min_length=1, max_length=MAX_NAME)
    media_type: Stored = Field(min_length=1, max_length=MAX_NAME)
    size: int = Field(ge=0)
    hash: Stored = Field(min_length=1, max_length=MAX_NAME)


class Children(Platform):
    """What belongs to a step without being its main content: the model's
    thinking, and the placeholders of the attachments its blocks name."""

    thinking: tuple[ThinkingBlock, ...] = ()
    attachments: tuple[Attachment, ...] = ()
