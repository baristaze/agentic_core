"""What crosses the provider boundary: a call the engine renders, the parts a
provider streams back, and the reply they end in. Each is made of the one
content shape and the boundary's names; a provider's payload never leaves
its adapter."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from acme.infra.base import FrozenMapping, InfraModel
from acme.integrations.model_providers.content import (
    MAX_NAME,
    Block,
    ReplyBlock,
    TextBlock,
    ThinkingBlock,
)
from acme.integrations.model_providers.types import Dropped, Effort, StopReason, Usage


class ToolSpec(InfraModel):
    """A tool a call offers the model, in the order the engine fixes."""

    name: str = Field(min_length=1, max_length=MAX_NAME)
    description: str = ""
    input_schema: FrozenMapping = Field(default_factory=dict, validate_default=True)


class FileData(InfraModel):
    """The bytes of an attachment a call's blocks name, read from blob storage
    for this call alone."""

    attachment_id: UUID
    name: str = Field(min_length=1, max_length=MAX_NAME)
    media_type: str = Field(min_length=1, max_length=MAX_NAME)
    data: bytes


class Message(InfraModel):
    """One turn of the conversation a call carries. `cache` marks the end of
    a stable prefix: a provider with caching places a breakpoint after it,
    and one without names the marker dropped."""

    role: Literal["user", "assistant"]
    blocks: tuple[Block, ...]
    cache: bool = False


class OutputSchema(InfraModel):
    """The shape a call expects its answer in, when not text."""

    name: str = Field(min_length=1, max_length=MAX_NAME)
    json_schema: FrozenMapping


class ModelCall(InfraModel):
    """One request to one model, rendered by the engine. `system_cache` marks
    the system blocks, and the tools before them, as a stable prefix."""

    model: str = Field(min_length=1, max_length=MAX_NAME)
    system: tuple[TextBlock, ...] = ()
    system_cache: bool = False
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...] = ()
    max_output_tokens: int = Field(gt=0)
    effort: Effort | None = None
    thinking_budget: int | None = Field(default=None, gt=0)
    output_schema: OutputSchema | None = None
    files: tuple[FileData, ...] = ()

    def file(self, attachment_id: UUID) -> FileData | None:
        return next((f for f in self.files if f.attachment_id == attachment_id), None)


class ModelReply(InfraModel):
    """A response, whole, as the engine records it: its main blocks, its
    thinking, why it stopped, and what it used. A reply cut by its output
    bound, or by a broken stream, is `truncated` and never complete; a reply
    with no stop reason is one the stream never finished."""

    blocks: tuple[ReplyBlock, ...] = ()
    thinking: tuple[ThinkingBlock, ...] = ()
    stop_reason: StopReason | None
    truncated: bool = False
    usage: Usage
    model: str = Field(min_length=1, max_length=MAX_NAME)  # as the provider named it
    response_id: str | None = Field(default=None, max_length=MAX_NAME)
    dropped: tuple[Dropped, ...] = ()

    @model_validator(mode="after")
    def _never_assumed_whole(self) -> Self:
        cut = self.stop_reason is None or self.stop_reason is StopReason.OUTPUT_LIMIT
        if cut and not self.truncated:
            raise ValueError("a reply with no stop reason, or cut by its bound, is truncated")
        return self


class TextDelta(InfraModel):
    kind: Literal["text"] = "text"
    index: int = Field(ge=0)  # the block the part belongs to
    text: str


class ThinkingDelta(InfraModel):
    kind: Literal["thinking"] = "thinking"
    index: int = Field(ge=0)
    text: str


class ToolUseDelta(InfraModel):
    """A fragment of a tool use's input, as JSON text; the input is whole only
    in the reply, and a fragment is never run."""

    kind: Literal["tool_use"] = "tool_use"
    index: int = Field(ge=0)
    id: str = Field(min_length=1, max_length=MAX_NAME)
    name: str = Field(min_length=1, max_length=MAX_NAME)
    partial_input: str = ""


class Finished(InfraModel):
    """The last part of every stream that ends: the reply, whole."""

    kind: Literal["finished"] = "finished"
    reply: ModelReply


StreamPart = Annotated[
    TextDelta | ThinkingDelta | ToolUseDelta | Finished, Field(discriminator="kind")
]
