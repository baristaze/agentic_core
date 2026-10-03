"""`ask_person`: the agent asks its person a question, or stands down and
says what it needs. The call answers the question back; the loop then
parks on `person` before any further model call (`agents.loop_rules.
question_waits`), and the person's next message is the answer that
resumes it (`agent_sessions.rules.QUESTION`). The question lives in the
history, in the call and its answer, never in the park."""

from datetime import timedelta

from pydantic import Field

from acme.om.base import Platform
from acme.om.context import TenantContext
from acme.om.tools.tool import ToolInterface, ToolRuntime
from acme.om.tools.types.policy import Target
from acme.om.tools.types.tool import Effect, ToolClass, ToolInput, ToolMode, ToolSpec

ASK_PERSON = "ask_person"
QUESTION_CHARS = 4_000

DESCRIPTION = (
    "Ask your person a question, or stand down: say what stops you and what "
    "you need from them. Your loop waits, holding nothing, until they answer, "
    "and their answer arrives as their next message. Ask only what you cannot "
    "find out yourself, and ask everything you need at once."
)


class AskPersonInput(ToolInput):
    question: str = Field(min_length=1, max_length=QUESTION_CHARS)


class Asked(Platform):
    """The question, as the person reads it in the session's history."""

    question: str


class AskPersonToolImpl(ToolInterface):
    """Acts on nothing: what it records is the question, and what the loop
    does with it is park."""

    def __init__(self) -> None:
        self._spec = ToolSpec(
            name=ASK_PERSON,
            description=DESCRIPTION,
            input_model=AskPersonInput,
            output_model=Asked,
            timeout=timedelta(seconds=10),
            authorization_class=ToolClass.READ,
            effect=Effect.IDEMPOTENT,
            interruptible=True,
            mode=ToolMode.SYNC,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def target(self, ctx: TenantContext, call_input: ToolInput) -> Target:
        return Target()

    async def preflight(
        self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime
    ) -> None:
        return None

    async def run(
        self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime
    ) -> Platform:
        assert isinstance(call_input, AskPersonInput)
        return Asked(question=call_input.question)
