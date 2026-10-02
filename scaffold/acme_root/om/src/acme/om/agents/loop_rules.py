"""Pure rules of a loop: which loop a session has open, what of it is still
unanswered, which controls ask something of it, how long a provider's
error is waited on, and the steps the engine writes of its own. Values in,
values out; no clock, no storage.

A loop's history is read the same way by the run that writes it and by a
run that takes it up after a crash, so the loop never needs to remember
where it was: the steps say it."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from acme.integrations.model_providers.calls import (
    ModelReply,
    TextDelta,
    ThinkingDelta,
    ToolSpec,
    ToolUseDelta,
)
from acme.integrations.model_providers.calls import StreamPart as ProviderPart
from acme.om.agents.types.kind import AgentKind
from acme.om.attribution.types.principal import Principal
from acme.om.steps.types.content import Children, Content, TextBlock, ToolUseBlock
from acme.om.steps.types.header import (
    ControlCommand,
    ControlHeader,
    InputHeader,
    LoopEndedHeader,
    LoopOutcome,
    MarkHeader,
    ModelResponseHeader,
    Park,
    ParkedHeader,
    ParkReason,
    ToolRequestHeader,
    ToolResponseHeader,
)
from acme.om.steps.types.step import Actor, Origin, Step, StepType
from acme.om.steps.types.stream import StreamPart, TextPart, ThinkingPart, ToolInputPart
from acme.om.tools.registry import ToolRegistry
from acme.om.windows.rules import exchanges
from acme.om.windows.types.kind import KindPrompts

NUDGE = (
    "Your turn ended without a tool call. Go on with the work, or submit your "
    "result through {tool}."
)
"""The engine's notice to a kind that ends its loops through a result tool,
when a turn neither continued nor submitted."""

PAUSED = "Your turn was paused before it ended. Go on from where it stopped."
"""The engine's notice after a provider paused a long turn."""

CUT = (
    "Your last reply reached its output limit and was cut before it ended, so "
    "it was not kept. Answer again within the limit: more briefly, or in parts "
    "over several turns."
)
"""The engine's notice after a reply was cut by its output bound: the reply
is kept truncated and never acted on, and the next request differs from the
one that was cut."""

HANDED_BACK = (
    "A person held this environment and worked in it by hand. It may not be "
    "as you last saw it. Their account follows as their message."
)
"""The engine's notice when a person gives the environment back."""

APPROVAL_UNLOCK = "approval"
SPENDER_UNLOCK = "spender"
PRINCIPAL_UNLOCK = "principal"
"""What a person-park the loop writes waits on: a decision on a call, a
principal who can pay, a principal who holds the calls."""


@dataclass(frozen=True)
class OpenLoop:
    """The loop a session has open: its id, the seq of its first step, and
    the park it waits on when its latest mark is one."""

    loop_id: UUID
    start_seq: int
    park: Park | None


def engine_written(step: Step) -> bool:
    """Whether a run wrote the step: everything but what arrives through the
    inbox, an input or a control, and the engine's own notices among the
    inputs."""
    if step.type.is_input():
        return step.actor is Actor.ENGINE
    return not step.type.is_control()


def open_loop(steps: Sequence[Step]) -> OpenLoop | None:
    """The loop a run wrote steps of and no `loop_ended` closed, or None.
    Inputs and controls carry ids of their own, so only what a run writes
    says which loop is open."""
    loop_id: UUID | None = None
    park: Park | None = None
    for step in steps:
        if not engine_written(step):
            continue
        if step.type is StepType.LOOP_ENDED:
            loop_id, park = None, None
            continue
        if step.loop_id != loop_id:
            loop_id, park = step.loop_id, None
        if isinstance(step.header, ParkedHeader):
            park = step.header.park
        elif step.type is StepType.RESUMED:
            park = None
    if loop_id is None:
        return None
    return OpenLoop(loop_id, start_seq(steps, loop_id), park)


def start_seq(steps: Sequence[Step], loop_id: UUID) -> int:
    """The seq of a loop's first step: its trigger, whose id is the loop's,
    else the first step that names the loop."""
    for step in steps:
        if step.id == loop_id or step.loop_id == loop_id:
            return step.seq
    return 0


def latest_response(steps: Sequence[Step], loop_id: UUID) -> Step | None:
    """The loop's latest complete response of the main model role: neither
    cut short nor abandoned."""
    ex = exchanges(steps)
    found = [response for response in ex.responses.values() if response.loop_id == loop_id]
    return max(found, key=lambda step: step.seq, default=None)


def open_calls(steps: Sequence[Step], response: Step) -> list[tuple[ToolUseBlock, Step | None]]:
    """Each tool use of `response` that no tool response answers yet, in the
    order the model made them, with its request when one was written."""
    requests: dict[str, Step] = {}
    answered: set[UUID] = set()
    for step in steps:
        header = step.header
        if isinstance(header, ToolRequestHeader) and response.id in step.refs:
            requests[header.tool_use_id] = step
        elif step.type is StepType.TOOL_RESPONSE and step.responds_to is not None:
            answered.add(step.responds_to)
    calls: list[tuple[ToolUseBlock, Step | None]] = []
    for use in response.as_tool_uses():
        request = requests.get(use.id)
        if request is None or request.id not in answered:
            calls.append((use, request))
    return calls


def accepted_outcome(steps: Sequence[Step], response: Step) -> LoopOutcome | None:
    """The outcome a result submitted in `response` was accepted with, as
    its answer in the history records it, or None."""
    asked = {
        step.id for step in steps if step.type is StepType.TOOL_REQUEST and response.id in step.refs
    }
    for step in steps:
        header = step.header
        if (
            isinstance(header, ToolResponseHeader)
            and step.responds_to in asked
            and header.accepted is not None
        ):
            return header.accepted.outcome
    return None


def judged(steps: Sequence[Step], response: Step) -> bool:
    """Whether a turn that called no tool was acted on: a step a run wrote
    in its loop follows it, such as a nudge."""
    return any(
        step.seq > response.seq and step.loop_id == response.loop_id and engine_written(step)
        for step in steps
    )


def nudges_before(steps: Sequence[Step], response: Step) -> int:
    """How many turns of the loop in a row, before `response`, neither
    called a tool nor were cut short."""
    count = 0
    for step in steps:
        header = step.header
        if step.seq >= response.seq or step.loop_id != response.loop_id:
            continue
        if not isinstance(header, ModelResponseHeader) or header.truncated or header.abandoned:
            continue
        called = any(block.kind == "tool_use" for block in step.content.blocks)
        count = 0 if called else count + 1
    return count


def asked(steps: Sequence[Step], loop: OpenLoop, command: ControlCommand) -> Step | None:
    """The latest `command` control since the loop began, or None."""
    found = None
    for step in steps:
        header = step.header
        if (
            step.seq > loop.start_seq
            and isinstance(header, ControlHeader)
            and header.command is command
        ):
            found = step
    return found


def pause_waits(steps: Sequence[Step], loop: OpenLoop) -> bool:
    """Whether a principal's pause waits for the loop's next safe point: a
    pause since the loop began, since its last pause-park, and not undone
    by a resume after it."""
    since = loop.start_seq
    for step in steps:
        header = step.header
        if isinstance(header, ParkedHeader) and header.park.reason is ParkReason.PAUSE:
            since = max(since, step.seq)
    latest: ControlCommand | None = None
    for step in steps:
        header = step.header
        if step.seq > since and isinstance(header, ControlHeader):
            if header.command in (ControlCommand.PAUSE, ControlCommand.RESUME):
                latest = header.command
    return latest is ControlCommand.PAUSE


def stops_call(steps: Sequence[Step], request: Step, interruptible: bool) -> ControlCommand | None:
    """The control that stops a running call: a cancel since its request,
    or, when its tool may be stopped, an interrupt that names this call's
    request. An interrupt binds to the call it names, so it stops no call
    after it."""
    for step in steps:
        header = step.header
        if step.seq <= request.seq or not isinstance(header, ControlHeader):
            continue
        if header.command is ControlCommand.CANCEL:
            return ControlCommand.CANCEL
        if header.command is ControlCommand.INTERRUPT and interruptible and request.id in step.refs:
            return ControlCommand.INTERRUPT
    return None


def retry_wait(retry_after: float | None, attempt: int, base: timedelta) -> timedelta:
    """How long the loop waits before it asks a provider again: a backoff
    that doubles with each attempt, and never less than the provider's own
    retry-after."""
    backoff = base * (2**attempt)
    if retry_after is None:
        return backoff
    return max(backoff, timedelta(seconds=retry_after))


def holds_private(kind: AgentKind, registry: ToolRegistry) -> bool:
    """Whether a session holds private data or credentials, for the rule of
    two: its kind says it holds private data, or a tool it may call is given
    a secret."""
    return kind.private_data or any(tool.spec.secrets for tool in registry.tools())


def kind_prompts(kind: AgentKind, registry: ToolRegistry) -> KindPrompts:
    """The first two layers of every request the kind renders: its prompts,
    and the tools its registry holds, in the registry's fixed order."""
    return KindPrompts(
        kind=kind.name,
        version=kind.version,
        prompts=kind.prompts,
        tools=tuple(
            ToolSpec(name=tool.name, description=tool.description, input_schema=tool.input_schema)
            for tool in registry.render()
        ),
    )


def stream_part(session_id: UUID, step_id: UUID, n: int, part: ProviderPart) -> StreamPart | None:
    """A provider's part as the loop emits it, numbered in its stream and
    naming the response it adds up to; None for the last part, the reply
    itself, which is stored and never streamed."""
    match part:
        case TextDelta():
            return TextPart(
                session_id=session_id, step_id=step_id, n=n, index=part.index, text=part.text
            )
        case ThinkingDelta():
            return ThinkingPart(
                session_id=session_id, step_id=step_id, n=n, index=part.index, text=part.text
            )
        case ToolUseDelta():
            return ToolInputPart(
                session_id=session_id,
                step_id=step_id,
                n=n,
                index=part.index,
                tool_use_id=part.id,
                tool=part.name,
                text=part.partial_input,
            )
        case _:
            return None


# What the engine writes.


def response_step(step_id: UUID, at: datetime, request: Step, reply: ModelReply) -> Step:
    """A model's reply as its request's response, saved once, whole, when its
    stream ends; a reply cut short is saved truncated, holding what arrived,
    and its tool uses are never run."""
    return Step(
        id=step_id,
        created_at=at,
        session_id=request.session_id,
        loop_id=request.loop_id,
        type=StepType.MODEL_RESPONSE,
        actor=Actor.MODEL,
        origin=Origin.ENGINE,
        responds_to=request.id,
        header=ModelResponseHeader(
            truncated=reply.truncated, usage=reply.usage, stop_reason=reply.stop_reason
        ),
        content=Content(blocks=reply.blocks),
        children=Children(thinking=reply.thinking),
    )


def abandoned_step(step_id: UUID, at: datetime, request: Step) -> Step:
    """The close of a request that never got its response: its call failed
    before the provider answered, or a run that made it was lost."""
    return Step(
        id=step_id,
        created_at=at,
        session_id=request.session_id,
        loop_id=request.loop_id,
        type=StepType.MODEL_RESPONSE,
        actor=Actor.ENGINE,
        origin=Origin.ENGINE,
        responds_to=request.id,
        header=ModelResponseHeader(abandoned=True),
    )


def notice_step(
    step_id: UUID, at: datetime, session_id: UUID, loop_id: UUID, principal: Principal, text: str
) -> Step:
    """The engine's notice to the model, a nudge among them: a message the
    engine writes under the run's epoch, on the authority the session's
    calls run under. It instructs, wakes nothing, and never pays, so the
    next request carries it between two of the model's turns."""
    return Step(
        id=step_id,
        created_at=at,
        session_id=session_id,
        loop_id=loop_id,
        type=StepType.MESSAGE,
        actor=Actor.ENGINE,
        origin=Origin.ENGINE,
        header=InputHeader(waking=False, principal=principal),
        content=Content(blocks=(TextBlock(text=text),)),
    )


def nudge_text(kind: AgentKind) -> str:
    return NUDGE.format(tool=kind.result_tool or "your result tool")


def ended_step(
    step_id: UUID, at: datetime, session_id: UUID, loop_id: UUID, outcome: LoopOutcome
) -> Step:
    """The step that closes a loop with its outcome."""
    return Step(
        id=step_id,
        created_at=at,
        session_id=session_id,
        loop_id=loop_id,
        type=StepType.LOOP_ENDED,
        actor=Actor.ENGINE,
        origin=Origin.ENGINE,
        header=LoopEndedHeader(outcome=outcome),
    )


def changed_step(step_id: UUID, at: datetime, session_id: UUID, loop_id: UUID) -> Step:
    """The notice that the world under the model changed: a person worked in
    its environment by hand."""
    return Step(
        id=step_id,
        created_at=at,
        session_id=session_id,
        loop_id=loop_id,
        type=StepType.ENVIRONMENT_CHANGED,
        actor=Actor.ENGINE,
        origin=Origin.ENGINE,
        header=MarkHeader(),
        content=Content(blocks=(TextBlock(text=HANDED_BACK),)),
    )


def unanswered_requests(steps: Sequence[Step], loop_id: UUID) -> list[Step]:
    """The loop's model requests, of any role, that no response answers:
    what a lost run left, one per call it had in flight."""
    answered = {step.responds_to for step in steps if step.type is StepType.MODEL_RESPONSE}
    return [
        step
        for step in steps
        if step.type is StepType.MODEL_REQUEST
        and step.loop_id == loop_id
        and step.id not in answered
    ]
