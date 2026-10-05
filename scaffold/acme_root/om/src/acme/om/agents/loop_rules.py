"""Pure rules of a loop: which loop a session has open, what of it is still
unanswered, which controls ask something of it, how long a provider's
error is waited on, when a call that keeps failing earns a notice, and the
steps the engine writes of its own. Values in, values out; no clock, no
storage.

A loop's history is read the same way by the run that writes it and by a
run that takes it up after a crash, so the loop never needs to remember
where it was: the steps say it."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import ValidationError

from acme.integrations.model_providers.calls import (
    ModelReply,
    TextDelta,
    ThinkingDelta,
    ToolSpec,
    ToolUseDelta,
)
from acme.integrations.model_providers.calls import StreamPart as ProviderPart
from acme.om.agent_sessions.rules import held_private
from acme.om.agent_sessions.types.agent_session import AgentSession
from acme.om.agents.types.kind import AgentKind
from acme.om.agents.types.report import Report
from acme.om.attribution.rules import principal_authored
from acme.om.attribution.types.principal import Principal
from acme.om.steps.rules import completes
from acme.om.steps.types.content import Children, Content, TextBlock, ToolUseBlock
from acme.om.steps.types.header import (
    ControlCommand,
    ControlHeader,
    InputHeader,
    JobPark,
    LoopEndedHeader,
    LoopOutcome,
    MarkHeader,
    ModelRequestHeader,
    ModelResponseHeader,
    Park,
    ParkedHeader,
    ParkReason,
    ToolRequestHeader,
    ToolResponseHeader,
)
from acme.om.steps.types.step import Actor, Origin, Step, StepType
from acme.om.steps.types.stream import StreamPart, TextPart, ThinkingPart, ToolInputPart
from acme.om.tools.native.ask_person import ASK_PERSON
from acme.om.tools.native.wait_for_sub_agents import WAIT_FOR_SUB_AGENTS
from acme.om.tools.registry import ToolRegistry
from acme.om.tools.types.call import JobCompletion
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

REPEATED = (
    "The call {tool} failed {count} times in a row with the same input, and "
    "the same call will fail the same way again. Read its last error, then "
    "change the input, use another tool, or check what you assumed first."
)
"""The engine's notice when the model repeats a call that keeps failing.
The error streak still bounds the loop; the notice comes first."""

APPROVAL_UNLOCK = "approval"
SPENDER_UNLOCK = "spender"
PRINCIPAL_UNLOCK = "principal"
"""What a person-park the loop writes waits on: a decision on a call, a
principal who can pay, a principal who holds the calls."""


JOB_DEADLINE = "the job did not complete by its deadline, and was cancelled"
"""What a job's call answers when its deadline passes with no completion."""


@dataclass(frozen=True)
class StartedJob:
    """A job a loop parked on whose call no response answers yet: the
    call's request, the park that names the job, and the first completion
    that names it by its key and handle, when one arrived."""

    request: Step
    park: Park
    completion: JobCompletion | None

    @property
    def job(self) -> JobPark:
        assert self.park.job is not None
        return self.park.job


def started_jobs(steps: Sequence[Step], loop_id: UUID) -> dict[UUID, StartedJob]:
    """Each job the loop parked on whose call is still open, by its key. A
    completion counts only when it is the event that names the call's
    request, and what it holds names the same key and the handle the park
    keeps: anything else that claims to end the job ends nothing."""
    requests: dict[UUID, Step] = {}
    answered: set[UUID] = set()
    parks: dict[UUID, Park] = {}
    reports: dict[UUID, list[Step]] = {}
    for step in steps:
        header = step.header
        if isinstance(header, ToolRequestHeader) and step.loop_id == loop_id:
            requests[step.id] = step
        elif step.type is StepType.TOOL_RESPONSE and step.responds_to is not None:
            answered.add(step.responds_to)
        elif isinstance(header, ParkedHeader) and step.loop_id == loop_id:
            if header.park.job is not None:
                parks.setdefault(header.park.job.key, header.park)
        else:
            ended = completes(step)
            if ended is not None:
                reports.setdefault(ended, []).append(step)
    found: dict[UUID, StartedJob] = {}
    for key, park in parks.items():
        request = requests.get(key)
        if request is None or key in answered or park.job is None:
            continue
        handle = park.job.handle
        completion = next(
            (
                said
                for said in (_completion(step) for step in reports.get(key, ()))
                if said is not None and said.key == key and said.handle == handle
            ),
            None,
        )
        found[key] = StartedJob(request, park, completion)
    return found


def _completion(step: Step) -> JobCompletion | None:
    try:
        return JobCompletion.model_validate_json(step.as_text())
    except ValidationError:
        return None


def asked_by(steps: Sequence[Step], request: Step) -> Principal | None:
    """Who pays for a job a call starts: the spender of the model request
    whose response asked for the call. None when the history holds no
    such request."""
    by_id = {step.id: step for step in steps}
    for ref in request.refs:
        response = by_id.get(ref)
        if response is None or response.responds_to is None:
            continue
        asked = by_id.get(response.responds_to)
        if asked is not None and isinstance(asked.header, ModelRequestHeader):
            return asked.header.spender
    return None


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


def report_of(
    steps: Sequence[Step],
    loop: OpenLoop,
    *,
    outcome: LoopOutcome | None = None,
    park: Park | None = None,
) -> Report:
    """What a child tells its parent of `loop` as `steps` hold it, from its
    first step: the outcome it ended with, or the park it waits on; the
    result its gate accepted with that outcome; the text of its latest
    complete response that said something; and whether the cancel that
    ended it came down from its parent."""
    ex = exchanges(steps)
    responses = sorted(
        (step for step in ex.responses.values() if step.loop_id == loop.loop_id),
        key=lambda step: step.seq,
        reverse=True,
    )
    answer = next((text for step in responses if (text := step.as_text().strip())), None)
    accepted = None
    for step in steps:
        header = step.header
        if (
            outcome is not None
            and step.loop_id == loop.loop_id
            and isinstance(header, ToolResponseHeader)
            and header.accepted is not None
            and header.accepted.outcome is outcome
        ):
            accepted = header.accepted
    cancel = asked(steps, loop, ControlCommand.CANCEL)
    by_parent = (
        outcome is LoopOutcome.CANCELLED and cancel is not None and cancel.origin is Origin.PARENT
    )
    return Report(
        loop_id=loop.loop_id,
        outcome=outcome,
        park=park,
        accepted=accepted,
        answer=answer,
        cancelled_by_parent=by_parent,
    )


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


def question_waits(steps: Sequence[Step], response: Step) -> bool:
    """Whether the agent asked its person in `response` and waits for the
    answer: the history answers an `ask_person` call of the response
    without a failure, and no principal's message has landed since the
    request the response answers, so none is one the model read."""
    if not answered_call(steps, response, ASK_PERSON):
        return False
    return not any(principal_authored(step) for step in woken_since(steps, response))


def children_wait(steps: Sequence[Step], response: Step) -> bool:
    """Whether the agent waits on its sub-agents after `response`: the
    history answers a `wait_for_sub_agents` call of the response without a
    failure, and no waking input has landed since the request the response
    answers, a child's report or a principal's message, so the model has
    read everything that has come."""
    if not answered_call(steps, response, WAIT_FOR_SUB_AGENTS):
        return False
    return not woken_since(steps, response)


def answered_call(steps: Sequence[Step], response: Step, tool: str) -> bool:
    """Whether the history answers a call of `tool` that `response` made
    without a failure."""
    made = {
        step.id
        for step in steps
        if isinstance(step.header, ToolRequestHeader)
        and step.header.tool == tool
        and response.id in step.refs
    }
    return any(
        isinstance(step.header, ToolResponseHeader)
        and step.header.failure is None
        and step.responds_to in made
        for step in steps
    )


def woken_since(steps: Sequence[Step], response: Step) -> list[Step]:
    """The waking inputs that landed after the request `response` answers,
    which the model has not read."""
    since = next((step.seq for step in steps if step.id == response.responds_to), response.seq)
    return [
        step
        for step in steps
        if step.seq > since and isinstance(step.header, InputHeader) and step.header.waking
    ]


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


def retry_wait(
    retry_after: float | None, attempt: int, base: timedelta, jitter: float
) -> timedelta:
    """How long the loop waits before it asks a provider again: a backoff
    that doubles with each attempt, half of it fixed and half drawn from
    `jitter`, a number in [0, 1), so sessions that failed together do not
    ask again together; and never less than the provider's own
    retry-after. The fixed half keeps the growth."""
    full = base * (2**attempt)
    backoff = full / 2 + (full / 2) * jitter
    if retry_after is None:
        return backoff
    return max(backoff, timedelta(seconds=retry_after))


def repeated_failure(steps: Sequence[Step], loop_id: UUID, every: int) -> str | None:
    """The notice the loop writes when its latest tool answers are the same
    call failing `every` times in a row, or a multiple of it, and no notice
    of the engine's followed the latest of them yet; else None. The same
    call is the same tool with the same input, by its hash."""
    calls: dict[UUID, tuple[str, str]] = {}
    last: tuple[str, str] | None = None
    count = answered_at = noticed_at = 0
    for step in steps:
        if step.loop_id != loop_id:
            continue
        header = step.header
        call = calls.get(step.responds_to) if step.responds_to is not None else None
        if isinstance(header, ToolRequestHeader):
            calls[step.id] = (header.tool, header.input_hash)
        elif step.type is StepType.TOOL_RESPONSE and call is not None:
            if step.as_tool_response().is_error:
                count, last = (count + 1 if call == last else 1), call
            else:
                count, last = 0, None
            answered_at = step.seq
        elif step.type is StepType.MESSAGE and step.actor is Actor.ENGINE:
            noticed_at = step.seq
    if last is None or count % every or noticed_at > answered_at:
        return None
    return REPEATED.format(tool=last[0], count=count)


def holds_private(
    session: AgentSession, kind: AgentKind, registry: ToolRegistry, history: Sequence[Step]
) -> bool:
    """Whether a session holds private data or credentials, for the rule of
    two: its kind says it holds private data, a tool it may call is given a
    secret, it took the mark from a session it came from that holds either,
    or its history holds a child's report that carries it, one that landed
    after the run read its session included."""
    return (
        held_private(session.holds_private, history)
        or kind.private_data
        or any(tool.spec.secrets for tool in registry.tools())
    )


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
