"""Stream parts: the live fragments of what a step will hold, so a person can
watch an agent think and steer on what it is thinking now.

A part is typed (a text delta, a thinking delta, a fragment of a tool
call's input, a chunk of a tool's output), numbered within its stream, and
carries the id of the step it adds up to. A part is never stored as a step
or an event: the step is stored once, whole, when its stream ends, and a
stream that breaks still ends in a step, marked truncated, holding what
arrived. The parts of one step are one stream, numbered from 0."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from acme.om.base import Platform
from acme.om.steps.types.content import MAX_NAME


class PartBase(Platform):
    session_id: UUID
    step_id: UUID  # the step the part adds up to
    n: int = Field(ge=0)  # its place in its stream


class TextPart(PartBase):
    """A piece of a model response's text block `index`."""

    kind: Literal["text"] = "text"
    index: int = Field(ge=0)
    text: str


class ThinkingPart(PartBase):
    """A piece of a model response's thinking block `index`."""

    kind: Literal["thinking"] = "thinking"
    index: int = Field(ge=0)
    text: str


class ToolInputPart(PartBase):
    """A fragment of a tool use's input as JSON text, as the model writes it.
    It is never run: a tool runs on the whole input, from the stored
    response."""

    kind: Literal["tool_input"] = "tool_input"
    index: int = Field(ge=0)
    tool_use_id: str = Field(min_length=1, max_length=MAX_NAME)
    tool: str = Field(min_length=1, max_length=MAX_NAME)
    text: str = ""


class ToolOutputPart(PartBase):
    """A chunk of what a running tool prints, on its channel (`stdout`,
    `stderr`), redacted before it streams. It adds up to the call's
    response."""

    kind: Literal["tool_output"] = "tool_output"
    channel: str = Field(min_length=1, max_length=MAX_NAME)
    text: str


StreamPart = Annotated[
    TextPart | ThinkingPart | ToolInputPart | ToolOutputPart, Field(discriminator="kind")
]
