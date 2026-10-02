"""What a step says: one provider-neutral block model, and the children that
belong to a step without being its main content.

The blocks are the provider boundary's own (ADR 1005): text, image,
document, thinking, tool use, and tool result, told apart by their `kind`
and validated as they are built, so nothing past an adapter reads a loose
dictionary. There is no second content type. An image or a document is a
reference to an attachment, never its bytes: the step holds the
attachment's placeholder among its children, and the bytes live in blob
storage. Every string a block or a placeholder holds is one both storage
impls keep as it is (`Stored`).

What a step says is plain in memory and sealed at rest. A layer the rest of
the engine never sees seals it on its way into storage and opens it on its
way out, so a step in hand holds its blocks, or, where nothing holds them
any more, says so (`ContentState`)."""

from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from acme.integrations.model_providers.content import MAX_NAME as MAX_NAME
from acme.integrations.model_providers.content import UNPARSED as UNPARSED
from acme.integrations.model_providers.content import Block as Block
from acme.integrations.model_providers.content import DocumentBlock as DocumentBlock
from acme.integrations.model_providers.content import ImageBlock as ImageBlock
from acme.integrations.model_providers.content import ResultPart as ResultPart
from acme.integrations.model_providers.content import Stored as Stored
from acme.integrations.model_providers.content import StoredMapping as StoredMapping
from acme.integrations.model_providers.content import TextBlock as TextBlock
from acme.integrations.model_providers.content import ThinkingBlock as ThinkingBlock
from acme.integrations.model_providers.content import ToolResultBlock as ToolResultBlock
from acme.integrations.model_providers.content import ToolUseBlock as ToolUseBlock
from acme.integrations.model_providers.content import in_turn_order as in_turn_order
from acme.integrations.model_providers.content import storable as storable
from acme.integrations.model_providers.content import storable_value as storable_value
from acme.om.base import Platform


class ContentState(StrEnum):
    """Where what a step says is. The state answers, so no caller compares
    strings."""

    PLAIN = "plain"  # the blocks and the children are what was said
    SEALED = "sealed"  # at rest: `sealed` holds the blocks and the children
    ABSENT = "absent"  # nothing holds them: a hole in a known place


class Sealed(Platform):
    """A step's blocks and children, sealed at rest under one version of its
    session's key and bound to the step. Only storage holds a step in this
    state; a read opens it."""

    version: int = Field(ge=1)  # the version of the session's key
    ciphertext: str = Field(min_length=1, repr=False)  # URL-safe base64


class Content(Platform):
    """A step's main content: its blocks, in order. Plain in memory; sealed
    at rest. A step whose session's key is revoked, or whose session keeps
    its content in memory only and has none here, reads as absent: its
    blocks and its children are empty, and its shape stays."""

    blocks: tuple[Block, ...] = ()
    state: ContentState = ContentState.PLAIN
    sealed: Sealed | None = None

    @model_validator(mode="after")
    def _one_place(self) -> Self:
        if (self.state is ContentState.SEALED) != (self.sealed is not None):
            raise ValueError("sealed content carries its seal, and nothing else does")
        if self.state is not ContentState.PLAIN and self.blocks:
            raise ValueError(f"{self.state.value} content holds no block in the clear")
        return self

    def is_plain(self) -> bool:
        return self.state is ContentState.PLAIN

    def is_sealed(self) -> bool:
        return self.state is ContentState.SEALED

    def is_absent(self) -> bool:
        return self.state is ContentState.ABSENT


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
