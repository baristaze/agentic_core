"""`write_plan`: the agent writes its plan, the steps it will take and where
it stands. Each call the history answers without a failure is one version
of the plan, kept in the history like any call: the model's own words in
its response, and the plan again in the call's answer, which a person
reads among the session's steps. The current plan is a projection of the
history (`current_plan`), never a second copy, and the loop renders it last
in each request of the main model role."""

from collections.abc import Sequence
from datetime import timedelta
from uuid import UUID

from pydantic import Field

from acme.om.base import Platform
from acme.om.context import TenantContext
from acme.om.steps.types.header import ToolRequestHeader, ToolResponseHeader
from acme.om.steps.types.step import Step, StepType
from acme.om.tools.tool import ToolInterface, ToolRuntime
from acme.om.tools.types.policy import Target
from acme.om.tools.types.tool import Effect, ToolClass, ToolInput, ToolSpec

WRITE_PLAN = "write_plan"
PLAN_CHARS = 8_000

DESCRIPTION = (
    "Write your plan: the steps you will take, and where you stand in them. "
    "It replaces the plan you wrote before, it is shown to you at the end of "
    "every request from now on, and your person reads it. Keep it short, and "
    "write it again when it changes."
)


class WritePlanInput(ToolInput):
    plan: str = Field(min_length=1, max_length=PLAN_CHARS)


class PlanKept(Platform):
    """The plan as the call's answer keeps it, for a person to read."""

    plan: str


class Plan(Platform):
    """The agent's current plan: its text, its version (how many plans the
    session has kept, this one included), and the answer that kept it."""

    version: int = Field(ge=1)
    text: str
    step_id: UUID


class WritePlanToolImpl(ToolInterface):
    """Acts on nothing outside the session's history: the call is the
    record, and writing the same plan again keeps the same plan."""

    def __init__(self) -> None:
        self._spec = ToolSpec(
            name=WRITE_PLAN,
            description=DESCRIPTION,
            input_model=WritePlanInput,
            output_model=PlanKept,
            timeout=timedelta(seconds=10),
            authorization_class=ToolClass.READ,
            effect=Effect.IDEMPOTENT,
            interruptible=True,
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
        assert isinstance(call_input, WritePlanInput)
        return PlanKept(plan=call_input.plan)


def current_plan(steps: Sequence[Step]) -> Plan | None:
    """The plan of the latest `write_plan` call the history answers without
    a failure, read from the response that made the call, in `seq` order. A
    call refused, denied, or cut off keeps no plan. None when the session
    kept none, or when the latest one's content is gone with its session's
    key."""
    written: dict[tuple[UUID, str], str] = {}  # (response, tool use) -> plan
    calls: dict[UUID, tuple[UUID, str] | None] = {}  # request -> its tool use
    version = 0
    latest: Plan | None = None
    for step in steps:
        header = step.header
        if step.type is StepType.MODEL_RESPONSE:
            for use in step.as_tool_uses():
                plan = use.input.get("plan")
                if use.name == WRITE_PLAN and isinstance(plan, str):
                    written[(step.id, use.id)] = plan
        elif isinstance(header, ToolRequestHeader) and header.tool == WRITE_PLAN:
            made = [(ref, header.tool_use_id) for ref in step.refs]
            calls[step.id] = next((key for key in made if key in written), None)
        elif isinstance(header, ToolResponseHeader) and header.failure is None:
            if step.responds_to is None or step.responds_to not in calls:
                continue
            version += 1
            key = calls[step.responds_to]
            text = None if key is None else written[key]
            latest = None if text is None else Plan(version=version, text=text, step_id=step.id)
    return latest
