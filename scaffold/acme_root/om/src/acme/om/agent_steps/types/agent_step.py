"""A step: the smallest thing the engine records, one event, written once.

A request and its response are two steps; a response points at its request
(`responds_to`), and a request never waits for its response to exist. No
step is two things and none copies another: a `model_request` references
the steps it carried, and a `tool_request` references the tool-use block of
the response that asked for it. A step's fields split into a header, its
shape, which stays readable, and its content, which is sealed at rest.

A step is checked against its type when it is built, so a step that breaks
its type's shape is refused there and never stored: the header its type
fixes, the blocks its type may hold, `responds_to` on a response alone, an
attachment's placeholder for every block that names one, the agent named
in the header of every step an agent produced, a tool request always
among them, and the head and tail of a result kept as an artifact."""

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, ClassVar, Self
from uuid import UUID

from pydantic import Field, model_validator

from acme.om.agent_steps.types.content import (
    Block,
    Children,
    Content,
    DocumentBlock,
    ImageBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    in_turn_order,
)
from acme.om.agent_steps.types.header import (
    InputHeader,
    StepHeader,
    SwitchedHeader,
    ToolRequestHeader,
    ToolResponseHeader,
)
from acme.om.base import Created, Identifiable


class AgentStepFamily(StrEnum):
    INPUT = "input"
    CONTROL = "control"
    MODEL = "model"
    TOOL = "tool"
    CONTEXT = "context"
    LIFECYCLE = "lifecycle"


class AgentStepType(StrEnum):
    """What a step records. The type answers questions, so no caller
    compares strings."""

    MESSAGE = "message"  # a principal speaking through a product, or a parent to its child
    EVENT = "event"  # anything from outside: a webhook, a callback, a job's completion
    CONTROL = "control"  # pause, resume, cancel, interrupt, compact, approve, deny, unlock, restore
    MODEL_REQUEST = "model_request"
    MODEL_RESPONSE = "model_response"
    TOOL_REQUEST = "tool_request"
    TOOL_RESPONSE = "tool_response"
    SUMMARY = "summary"  # replaces a range for reading
    PARKED = "parked"
    RESUMED = "resumed"
    LOOP_ENDED = "loop_ended"
    # A new fill set or kind version, or a toolset loaded, which the engine
    # records; or standing text, which the session's maker pins through the
    # inbox, and which instructs.
    SWITCHED = "switched"
    ENVIRONMENT_CHANGED = "environment_changed"  # the world under the model changed
    SNAPSHOTTED = "snapshotted"  # the session holds a snapshot of a workspace

    @property
    def family(self) -> AgentStepFamily:
        return FAMILIES[self]

    def is_input(self) -> bool:
        return self.family is AgentStepFamily.INPUT

    def is_control(self) -> bool:
        return self.family is AgentStepFamily.CONTROL

    def is_model_call(self) -> bool:
        return self.family is AgentStepFamily.MODEL

    def is_tool_call(self) -> bool:
        return self.family is AgentStepFamily.TOOL

    def is_summary(self) -> bool:
        return self is AgentStepType.SUMMARY

    def is_lifecycle(self) -> bool:
        return self.family is AgentStepFamily.LIFECYCLE

    def is_response(self) -> bool:
        return self in (AgentStepType.MODEL_RESPONSE, AgentStepType.TOOL_RESPONSE)


FAMILIES: dict[AgentStepType, AgentStepFamily] = {
    AgentStepType.MESSAGE: AgentStepFamily.INPUT,
    AgentStepType.EVENT: AgentStepFamily.INPUT,
    AgentStepType.CONTROL: AgentStepFamily.CONTROL,
    AgentStepType.MODEL_REQUEST: AgentStepFamily.MODEL,
    AgentStepType.MODEL_RESPONSE: AgentStepFamily.MODEL,
    AgentStepType.TOOL_REQUEST: AgentStepFamily.TOOL,
    AgentStepType.TOOL_RESPONSE: AgentStepFamily.TOOL,
    AgentStepType.SUMMARY: AgentStepFamily.CONTEXT,
    AgentStepType.PARKED: AgentStepFamily.LIFECYCLE,
    AgentStepType.RESUMED: AgentStepFamily.LIFECYCLE,
    AgentStepType.LOOP_ENDED: AgentStepFamily.LIFECYCLE,
    AgentStepType.SWITCHED: AgentStepFamily.LIFECYCLE,
    AgentStepType.ENVIRONMENT_CHANGED: AgentStepFamily.LIFECYCLE,
    AgentStepType.SNAPSHOTTED: AgentStepFamily.LIFECYCLE,
}

HEADER_KINDS: dict[AgentStepType, str] = {
    AgentStepType.MESSAGE: "input",
    AgentStepType.EVENT: "input",
    AgentStepType.CONTROL: "control",
    AgentStepType.MODEL_REQUEST: "model_request",
    AgentStepType.MODEL_RESPONSE: "model_response",
    AgentStepType.TOOL_REQUEST: "tool_request",
    AgentStepType.TOOL_RESPONSE: "tool_response",
    AgentStepType.SUMMARY: "summary",
    AgentStepType.PARKED: "parked",
    AgentStepType.RESUMED: "mark",
    AgentStepType.LOOP_ENDED: "loop_ended",
    AgentStepType.SWITCHED: "switched",
    AgentStepType.ENVIRONMENT_CHANGED: "mark",
    AgentStepType.SNAPSHOTTED: "snapshot",
}
"""The header each type holds."""

TEXT_AND_FILES = frozenset({"text", "image", "document"})

BLOCK_KINDS: dict[AgentStepType, frozenset[str]] = {
    AgentStepType.MESSAGE: TEXT_AND_FILES,
    AgentStepType.EVENT: TEXT_AND_FILES,
    AgentStepType.CONTROL: frozenset({"text"}),  # a reason given with a decision
    AgentStepType.MODEL_REQUEST: frozenset(),  # it references what it carried
    AgentStepType.MODEL_RESPONSE: frozenset({"text", "tool_use"}),  # its thinking is a child
    AgentStepType.TOOL_REQUEST: frozenset(),  # it references the tool use
    AgentStepType.TOOL_RESPONSE: frozenset({"tool_result"}),
    AgentStepType.SUMMARY: frozenset({"text"}),
    AgentStepType.PARKED: frozenset(),
    AgentStepType.RESUMED: frozenset(),
    AgentStepType.LOOP_ENDED: frozenset(),
    AgentStepType.SWITCHED: frozenset({"text"}),  # the standing text it pins, and only that
    AgentStepType.ENVIRONMENT_CHANGED: frozenset({"text"}),  # what a person did, or a restore
    AgentStepType.SNAPSHOTTED: frozenset(),
}
"""The blocks each type's content may hold."""


WAKES_BY_DEFAULT: dict[AgentStepType, bool] = {
    AgentStepType.MESSAGE: True,
    AgentStepType.EVENT: False,
}
"""Whether an input built with no `waking` wakes its session: a principal's
message does, and an event from outside does not."""


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


class AgentStep(Identifiable, Created):
    MANAGER_OWNED_FIELDS: ClassVar[tuple[str, ...]] = ("seq",)
    """The append assigns `seq`; whatever a caller sets is not read."""

    agent_session_id: UUID
    # Gapless per session, from the session's cursor row; 0 until storage
    # assigns it on append.
    seq: int = Field(default=0, ge=0)
    loop_id: UUID  # the id of its loop's first step
    type: AgentStepType
    actor: Actor
    origin: Origin
    responds_to: UUID | None = None  # a response's request
    refs: tuple[UUID, ...] = ()  # the steps it references, such as the inputs a request delivered
    header: StepHeader
    content: Content = Content()
    children: Children = Children()

    @model_validator(mode="before")
    @classmethod
    def _an_input_wakes_by_its_type(cls, data: Any) -> Any:
        """An input whose header leaves `waking` None takes its type's
        default, so a stored input always says whether it woke."""
        if not isinstance(data, Mapping):
            return data
        try:
            wakes = WAKES_BY_DEFAULT.get(AgentStepType(data.get("type")))
        except ValueError:
            return data  # the field's own validation names the type it refuses
        header = data.get("header")
        if wakes is None:
            return data
        if isinstance(header, InputHeader) and header.waking is None:
            return {**data, "header": header.model_copy(update={"waking": wakes})}
        if isinstance(header, Mapping) and header.get("kind") == "input":
            if header.get("waking") is None:
                return {**data, "header": {**header, "waking": wakes}}
        return data

    @model_validator(mode="before")
    @classmethod
    def _a_file_marks_its_input(cls, data: Any) -> Any:
        """An input that carries a file says so in its header (`untrusted`),
        whoever attached it: a file is data, so the session that reads it is
        marked, and the shape says so after its content is sealed or gone."""
        if not isinstance(data, Mapping):
            return data
        children = data.get("children")
        if isinstance(children, Children):
            files = bool(children.attachments)
        else:
            files = isinstance(children, Mapping) and bool(children.get("attachments"))
        header = data.get("header")
        if not files:
            return data
        if isinstance(header, InputHeader) and not header.untrusted:
            return {**data, "header": header.model_copy(update={"untrusted": True})}
        if isinstance(header, Mapping) and header.get("kind") == "input":
            if not header.get("untrusted"):
                return {**data, "header": {**header, "untrusted": True}}
        return data

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
        self._require(AgentStepType.MODEL_RESPONSE)
        return tuple(block for block in self.content.blocks if isinstance(block, ToolUseBlock))

    def as_turn(self) -> tuple[Block, ...]:
        """A model response's thinking and blocks in the order its provider
        sent them, each thinking block at the place it keeps (`at`): the
        turn as the next request replays it."""
        self._require(AgentStepType.MODEL_RESPONSE)
        return in_turn_order((*self.children.thinking, *self.content.blocks))

    def as_tool_response(self) -> ToolResultBlock:
        """A tool response's one result."""
        self._require(AgentStepType.TOOL_RESPONSE)
        (result,) = self.content.blocks
        if not isinstance(result, ToolResultBlock):
            raise ValueError("a tool response holds its result")
        return result

    def _require(self, step_type: AgentStepType) -> None:
        if self.type is not step_type:
            raise ValueError(f"a {self.type.value} step is not a {step_type.value}")


def switches_standing(step: AgentStep) -> bool:
    """Whether a step pins the session's standing text. Its maker sends it
    through the inbox, as it sends an input, so it says nothing of a run."""
    return isinstance(step.header, SwitchedHeader) and step.header.standing is not None


def switches_session(step: AgentStep) -> bool:
    """Whether a step switches what a person sets on the session itself: its
    standing text, its steerer, or who sees it. Each is sent through the
    inbox, outside any run, so it says nothing of a run."""
    header = step.header
    return isinstance(header, SwitchedHeader) and (
        header.standing is not None or header.steer is not None or header.visibility is not None
    )


def shape_refusal(step: AgentStep) -> str | None:
    """Why a step breaks its type's shape, or None when it fits. Content that
    is sealed or absent holds nothing in the clear, so only its shape is
    checked."""
    if step.header.kind != HEADER_KINDS[step.type]:
        return f"its header is {step.header.kind}, not {HEADER_KINDS[step.type]}"
    if step.type.is_response() != (step.responds_to is not None):
        return "a response names its request, and nothing else does"
    if step.type is AgentStepType.TOOL_REQUEST and not step.refs:
        return "it references the response that asked for it"
    if step.id in step.refs or step.responds_to == step.id:
        return "it references itself"
    header = step.header
    agent = header.agent if isinstance(header, InputHeader | ToolRequestHeader) else None
    if (step.actor is Actor.AGENT) != (agent is not None):
        return "an agent's step names the agent in its header, and no other step names one"
    if (
        isinstance(header, ToolRequestHeader)
        and header.agent.agent_session_id != step.agent_session_id
    ):
        return "a tool request is the act of its own session's agent"
    if not step.content.is_plain():
        if step.children != Children():
            return f"its content is {step.content.state.value}, and its children with it"
        return None
    kinds = [block.kind for block in step.content.blocks]
    allowed = BLOCK_KINDS[step.type]
    if stray := sorted(set(kinds) - allowed):
        return f"its content may not hold {', '.join(stray)}"
    if isinstance(header, SwitchedHeader) and header.standing is None and kinds:
        return "only a switch of the standing text holds text"
    if isinstance(header, SwitchedHeader) and header.standing is not None:
        chars = header.standing.chars
        if len(kinds) != (1 if chars else 0) or len(step.as_text()) != chars:
            return f"it holds its standing text of {chars} characters as one block"
    if step.type is AgentStepType.TOOL_RESPONSE and kinds != ["tool_result"]:
        return "it holds exactly one tool result"
    uses = [block.id for block in step.content.blocks if isinstance(block, ToolUseBlock)]
    if len(set(uses)) != len(uses):
        return "two tool uses share an id"
    if step.children.thinking and step.type is not AgentStepType.MODEL_RESPONSE:
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
    if isinstance(step.header, ToolResponseHeader) and step.header.artifact is not None:
        (result,) = step.content.blocks
        preview = result.parts[:2] if isinstance(result, ToolResultBlock) else ()
        if len(preview) != 2 or not all(isinstance(part, TextBlock) for part in preview):
            return "a result kept as an artifact holds its head and its tail first"
    return None
