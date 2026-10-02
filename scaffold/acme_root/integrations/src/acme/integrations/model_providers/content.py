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
document is a reference to an attachment, never its bytes."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from acme.infra.base import FrozenMapping, InfraModel
from acme.integrations.model_providers.types import ProviderName

MAX_NAME = 200
"""The longest tool name or tool-use id a block carries: a name a model
writes is bounded before it is stored."""


class TextBlock(InfraModel):
    kind: Literal["text"] = "text"
    text: str


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
    model: str = Field(min_length=1, max_length=MAX_NAME)


class ThinkingBlock(InfraModel):
    """The model's thinking: a child of the response that thought it, never
    its main content. `signature` is the provider's proof of it, opaque to
    the engine, and a block replays only with it, to the model that thought
    it. A `redacted` block holds no readable text, only its signature. `ref`
    is the provider's own id for the block, where its replay needs one."""

    kind: Literal["thinking"] = "thinking"
    text: str = ""
    signature: str | None = None
    source: ThinkingSource | None = None
    redacted: bool = False
    ref: str | None = Field(default=None, max_length=MAX_NAME)

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
    id: str = Field(min_length=1, max_length=MAX_NAME)
    name: str = Field(min_length=1, max_length=MAX_NAME)
    input: FrozenMapping = Field(default_factory=dict, validate_default=True)


ResultPart = Annotated[TextBlock | ImageBlock | DocumentBlock, Field(discriminator="kind")]
"""What a tool's result may hold."""


class ToolResultBlock(InfraModel):
    """What a tool returned for one tool use, or the failure it met."""

    kind: Literal["tool_result"] = "tool_result"
    tool_use_id: str = Field(min_length=1, max_length=MAX_NAME)
    parts: tuple[ResultPart, ...] = ()
    is_error: bool = False


Block = Annotated[
    TextBlock | ImageBlock | DocumentBlock | ThinkingBlock | ToolUseBlock | ToolResultBlock,
    Field(discriminator="kind"),
]

ReplyBlock = Annotated[TextBlock | ToolUseBlock, Field(discriminator="kind")]
"""What a response's main content holds; its thinking is a child."""
