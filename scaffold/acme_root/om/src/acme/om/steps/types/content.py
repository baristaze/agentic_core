"""What a step says: one provider-neutral block model, and the children that
belong to a step without being its main content.

The blocks are the provider boundary's own (ADR 1005): text, image,
document, thinking, tool use, and tool result, told apart by their `kind`
and validated as they are built, so nothing past an adapter reads a loose
dictionary. There is no second content type. An image or a document is a
reference to an attachment, never its bytes: the step holds the
attachment's placeholder among its children, and the bytes live in blob
storage. Every string a block or a placeholder holds is one both storage
impls keep as it is (`Stored`)."""

from uuid import UUID

from pydantic import Field

from acme.integrations.model_providers.content import MAX_NAME as MAX_NAME
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
from acme.integrations.model_providers.content import storable as storable
from acme.integrations.model_providers.content import storable_value as storable_value
from acme.om.base import Platform


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
