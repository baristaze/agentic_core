"""The synthetic tools: one per tool class a shape names. Each answers at
once with the output its call asked for, so the replay waits on no tool:
the call's input says the output's size, and whether it fails. Nothing here
runs a process, touches a file, or reaches a network.

The output is words from the run's pool, opening with its marker, so a
tool output that reaches the object store in the clear shows."""

from __future__ import annotations

from datetime import timedelta

from acme.om.agent_steps.types.header import ToolFailure
from acme.om.base import OmBase
from acme.om.context import TenantContext
from acme.om.exceptions import ToolFailed
from acme.om.tools.tool import ToolInterface, ToolRuntime
from acme.om.tools.types.policy import Target
from acme.om.tools.types.tool import Effect, ToolClass, ToolInput, ToolSpec
from replay.shape import TOOL_CLASSES
from replay.words import Words

EFFECTS = {
    ToolClass.READ: Effect.READ_ONLY,
    ToolClass.WRITE: Effect.IDEMPOTENT,
    ToolClass.EXECUTE: Effect.IDEMPOTENT,
    ToolClass.NETWORK: Effect.READ_ONLY,
    ToolClass.INTEGRATION: Effect.IDEMPOTENT,
    ToolClass.SPAWN: Effect.IDEMPOTENT,
}
"""Each class's effect: a read changes nothing, and the rest are made
idempotent under the call's key, as the engine asks of a tool whose effect a
lost run may repeat."""

CLASSES = tuple(ToolClass(name) for name in TOOL_CLASSES)
"""The shape's classes, held to the engine's: a class the engine does not
know fails here, at import."""


def tool_name(tool_class: str) -> str:
    return f"{tool_class}_tool"


class Asked(ToolInput):
    """What the model wrote, and what the call answers: `size` characters of
    output, or a failure of that size."""

    text: str
    size: int
    failed: bool = False


class Answered(OmBase):
    text: str


class SyntheticTool(ToolInterface):
    record: list[str] | None = None
    """Where a check keeps each output answered, in order; None keeps none."""

    def __init__(self, tool_class: ToolClass, words: Words) -> None:
        self._words = words
        self._spec = ToolSpec(
            name=tool_name(tool_class.value),
            description=f"Answers at once, as a {tool_class.value} tool would, with the output asked.",
            input_model=Asked,
            output_model=Answered,
            timeout=timedelta(minutes=1),
            authorization_class=tool_class,
            effect=EFFECTS[tool_class],
            interruptible=True,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def target(self, ctx: TenantContext, call_input: ToolInput) -> Target:
        """It acts on nothing outside the session, and says so: an outward
        call waits for a person, and the replay plays no person's approval."""
        return Target(attributes={"outward": False})

    async def preflight(self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime) -> None:
        return None

    async def run(self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime) -> OmBase:
        assert isinstance(call_input, Asked)
        text = self._words.text(call_input.size)
        if SyntheticTool.record is not None:
            SyntheticTool.record.append(text)
        if call_input.failed:
            raise ToolFailed(ToolFailure.PERMANENT, text)
        return Answered(text=text)


def tools(words: Words) -> tuple[ToolInterface, ...]:
    return tuple(SyntheticTool(tool_class, words) for tool_class in CLASSES)
