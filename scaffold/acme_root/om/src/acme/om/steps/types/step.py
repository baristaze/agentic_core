"""A step: the smallest thing the engine records, one event, written once.

A request and its response are two steps; a response points at its request
(`responds_to`), and a request never waits for its response to exist. No
step is two things and none copies another: a `model_request` references
the steps it carried, and a `tool_request` references the tool-use block of
the response that asked for it. A step's fields split into a header, its
shape, which stays readable, and its content, which is sealed at rest.

A step is checked against its type when it is built, so a step that breaks
its type's shape is refused there and never stored: the header its type
fixes, the blocks its type may hold, `responds_to` on a response alone, and
an attachment's placeholder for every block that names one."""

from enum import StrEnum
from typing import ClassVar, Self
from uuid import UUID

from pydantic import Field, model_validator

from acme.om.base import Created, Identifiable
from acme.om.steps.types.content import (
    Children,
    Content,
    DocumentBlock,
    ImageBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from acme.om.steps.types.header import StepHeader


class StepFamily(StrEnum):
    INPUT = "input"
    CONTROL = "control"
    MODEL = "model"
    TOOL = "tool"
    CONTEXT = "context"
    LIFECYCLE = "lifecycle"


class StepType(StrEnum):
    """What a step records. The type answers questions, so no caller
    compares strings."""

    MESSAGE = "message"  # a principal speaking through a product, or a parent to its child
    EVENT = "event"  # anything from outside: a webhook, a callback, a job's completion
    CONTROL = "control"  # pause, resume, cancel, interrupt, compact, approve, deny, unlock
    MODEL_REQUEST = "model_request"
    MODEL_RESPONSE = "model_response"
    TOOL_REQUEST = "tool_request"
    TOOL_RESPONSE = "tool_response"
    SUMMARY = "summary"  # replaces a range for reading
    PARKED = "parked"
    RESUMED = "resumed"
    LOOP_ENDED = "loop_ended"
    SWITCHED = "switched"  # a new fill set or kind version
    ENVIRONMENT_CHANGED = "environment_changed"  # the world under the model changed

    @property
    def family(self) -> StepFamily:
        return FAMILIES[self]

    def is_input(self) -> bool:
        return self.family is StepFamily.INPUT

    def is_control(self) -> bool:
        return self.family is StepFamily.CONTROL

    def is_model_call(self) -> bool:
        return self.family is StepFamily.MODEL

    def is_tool_call(self) -> bool:
        return self.family is StepFamily.TOOL

    def is_summary(self) -> bool:
        return self is StepType.SUMMARY

    def is_lifecycle(self) -> bool:
        return self.family is StepFamily.LIFECYCLE

    def is_response(self) -> bool:
        return self in (StepType.MODEL_RESPONSE, StepType.TOOL_RESPONSE)


FAMILIES: dict[StepType, StepFamily] = {
    StepType.MESSAGE: StepFamily.INPUT,
    StepType.EVENT: StepFamily.INPUT,
    StepType.CONTROL: StepFamily.CONTROL,
    StepType.MODEL_REQUEST: StepFamily.MODEL,
    StepType.MODEL_RESPONSE: StepFamily.MODEL,
    StepType.TOOL_REQUEST: StepFamily.TOOL,
    StepType.TOOL_RESPONSE: StepFamily.TOOL,
    StepType.SUMMARY: StepFamily.CONTEXT,
    StepType.PARKED: StepFamily.LIFECYCLE,
    StepType.RESUMED: StepFamily.LIFECYCLE,
    StepType.LOOP_ENDED: StepFamily.LIFECYCLE,
    StepType.SWITCHED: StepFamily.LIFECYCLE,
    StepType.ENVIRONMENT_CHANGED: StepFamily.LIFECYCLE,
}

HEADER_KINDS: dict[StepType, str] = {
    StepType.MESSAGE: "input",
    StepType.EVENT: "input",
    StepType.CONTROL: "control",
    StepType.MODEL_REQUEST: "model_request",
    StepType.MODEL_RESPONSE: "model_response",
    StepType.TOOL_REQUEST: "tool_request",
    StepType.TOOL_RESPONSE: "tool_response",
    StepType.SUMMARY: "summary",
    StepType.PARKED: "parked",
    StepType.RESUMED: "mark",
    StepType.LOOP_ENDED: "loop_ended",
    StepType.SWITCHED: "mark",
    StepType.ENVIRONMENT_CHANGED: "mark",
}
"""The header each type holds."""

TEXT_AND_FILES = frozenset({"text", "image", "document"})

BLOCK_KINDS: dict[StepType, frozenset[str]] = {
    StepType.MESSAGE: TEXT_AND_FILES,
    StepType.EVENT: TEXT_AND_FILES,
    StepType.CONTROL: frozenset({"text"}),  # a reason given with a decision
    StepType.MODEL_REQUEST: frozenset(),  # it references what it carried
    StepType.MODEL_RESPONSE: frozenset({"text", "tool_use"}),  # its thinking is a child
    StepType.TOOL_REQUEST: frozenset(),  # it references the tool use
    StepType.TOOL_RESPONSE: frozenset({"tool_result"}),
    StepType.SUMMARY: frozenset({"text"}),
    StepType.PARKED: frozenset(),
    StepType.RESUMED: frozenset(),
    StepType.LOOP_ENDED: frozenset(),
    StepType.SWITCHED: frozenset(),
    StepType.ENVIRONMENT_CHANGED: frozenset({"text"}),  # what a person did there
}
"""The blocks each type's content may hold."""


class Actor(StrEnum):
    """Who produced a step."""

    PERSON = "person"
    PROGRAM = "program"
    AGENT = "agent"
    MODEL = "model"
    ENGINE = "engine"
    EXTERNAL = "external"


class Origin(StrEnum):
    """Where a step came in."""

    PORTAL = "portal"
    CLI = "cli"
    API = "api"
    INTEGRATION = "integration"
    AUTOMATION = "automation"
    PARENT = "parent"
    ENGINE = "engine"


class Step(Identifiable, Created):
    MANAGER_OWNED_FIELDS: ClassVar[tuple[str, ...]] = ("seq",)
    """The append assigns `seq`; whatever a caller sets is not read."""

    session_id: UUID
    # Gapless per session, from the session's cursor row; 0 until storage
    # assigns it on append.
    seq: int = Field(default=0, ge=0)
    loop_id: UUID  # the id of its loop's first step
    type: StepType
    actor: Actor
    origin: Origin
    responds_to: UUID | None = None  # a response's request
    refs: tuple[UUID, ...] = ()  # the steps it references, such as the inputs a request delivered
    header: StepHeader
    content: Content = Content()
    children: Children = Children()

    @model_validator(mode="after")
    def _fits_its_type(self) -> Self:
        refusal = shape_refusal(self)
        if refusal is not None:
            raise ValueError(f"a {self.type.value} step: {refusal}")
        return self

    def as_text(self) -> str:
        """The text of its blocks, in order, one block a line."""
        return "\n".join(
            block.text for block in self.content.blocks if isinstance(block, TextBlock)
        )

    def as_tool_uses(self) -> tuple[ToolUseBlock, ...]:
        """A model response's tool calls, in the order the model made them."""
        self._require(StepType.MODEL_RESPONSE)
        return tuple(block for block in self.content.blocks if isinstance(block, ToolUseBlock))

    def as_tool_response(self) -> ToolResultBlock:
        """A tool response's one result."""
        self._require(StepType.TOOL_RESPONSE)
        (result,) = self.content.blocks
        if not isinstance(result, ToolResultBlock):
            raise ValueError("a tool response holds its result")
        return result

    def _require(self, step_type: StepType) -> None:
        if self.type is not step_type:
            raise ValueError(f"a {self.type.value} step is not a {step_type.value}")


def shape_refusal(step: Step) -> str | None:
    """Why a step breaks its type's shape, or None when it fits. Content that
    is sealed or absent holds nothing in the clear, so only its shape is
    checked."""
    if step.header.kind != HEADER_KINDS[step.type]:
        return f"its header is {step.header.kind}, not {HEADER_KINDS[step.type]}"
    if step.type.is_response() != (step.responds_to is not None):
        return "a response names its request, and nothing else does"
    if step.type is StepType.TOOL_REQUEST and not step.refs:
        return "it references the response that asked for it"
    if step.id in step.refs or step.responds_to == step.id:
        return "it references itself"
    if not step.content.is_plain():
        if step.children != Children():
            return f"its content is {step.content.state.value}, and its children with it"
        return None
    kinds = [block.kind for block in step.content.blocks]
    allowed = BLOCK_KINDS[step.type]
    if stray := sorted(set(kinds) - allowed):
        return f"its content may not hold {', '.join(stray)}"
    if step.type is StepType.TOOL_RESPONSE and kinds != ["tool_result"]:
        return "it holds exactly one tool result"
    uses = [block.id for block in step.content.blocks if isinstance(block, ToolUseBlock)]
    if len(set(uses)) != len(uses):
        return "two tool uses share an id"
    if step.children.thinking and step.type is not StepType.MODEL_RESPONSE:
        return "only a model response carries thinking"
    held = {attachment.id for attachment in step.children.attachments}
    if len(held) != len(step.children.attachments):
        return "two attachments share an id"
    named = {
        part.attachment_id
        for block in step.content.blocks
        for part in (block.parts if isinstance(block, ToolResultBlock) else (block,))
        if isinstance(part, ImageBlock | DocumentBlock)
    }
    if missing := named - held:
        return f"no placeholder for attachment {sorted(str(i) for i in missing)[0]}"
    return None
