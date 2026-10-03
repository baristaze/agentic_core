"""A step's header: its shape, typed per type, readable while its content is
sealed. Each header names its `kind`, and a step holds the one its type
fixes (`steps.types.step.HEADER_KINDS`). A header carries ids, names,
counts, and flags, never what a person typed or a tool returned.

An input and a tool request name their principal, and a model request its
spender: the attribution the audit reads, typed where the step is made, so
no step is stored without it."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from acme.integrations.model_providers.types import StopReason, Usage
from acme.om.attribution.types.authority import AuthorityMode
from acme.om.attribution.types.principal import AgentRef, Principal
from acme.om.base import Platform
from acme.om.context import Role
from acme.om.models.types.fill import FillSwitch
from acme.om.steps.types.content import MAX_NAME, Stored


class ControlCommand(StrEnum):
    """What a `control` step records: a command that travels out of band."""

    PAUSE = "pause"  # parks the loop at its next safe point
    RESUME = "resume"  # clears a pause
    CANCEL = "cancel"  # ends the loop `cancelled`, and its children's
    INTERRUPT = "interrupt"  # stops a tool that can be stopped
    COMPACT = "compact"  # asks for a compaction before the next call
    APPROVE = "approve"  # a person's decision on one exact tool call
    DENY = "deny"
    UNLOCK = "unlock"  # clears any park, such as by a raised budget


class ParkReason(StrEnum):
    """Why a loop waits, and so what clears it."""

    PERSON = "person"  # a question, an approval, a deadline, a principal to reassign
    PROVIDER = "provider"  # an outage, a rate limit, a billing or credential error
    BUDGET = "budget"  # the gate refused
    RESOURCE = "resource"  # a scarce resource or a workspace, in line
    JOB = "job"  # a long-running tool job is working
    CHILDREN = "children"  # sub-agents have not reported
    HANDOVER = "handover"  # a person holds the environment
    PAUSE = "pause"  # a principal paused


class ToolFailure(StrEnum):
    """The class of a tool failure, decided where the failure happens. The
    model reads it with advice on what to do next."""

    INVALID_INPUT = "invalid_input"  # the input failed its schema; the model can correct it
    TRANSIENT = "transient"  # worth retrying
    TIMEOUT = "timeout"  # ran out of time
    DENIED = "denied"  # policy or a person said no
    INTERRUPTED = "interrupted"  # stopped mid-run, or its outcome is unknown
    PERMANENT = "permanent"  # will not work as asked


class LoopOutcome(StrEnum):
    """The five ways a loop ends. A park is none of them."""

    SUCCEEDED = "succeeded"  # the kind's done rule was met and its result gate accepted
    FAILED = "failed"  # a conclusion, with evidence, that the objective cannot be met
    INCONCLUSIVE = "inconclusive"  # stopped without a conclusion
    CANCELLED = "cancelled"  # a principal cancelled it
    ERRORED = "errored"  # an error no park can clear


PERSON_ONLY = frozenset({ParkReason.PERSON, ParkReason.HANDOVER, ParkReason.PAUSE})
"""The reasons only a person clears: a question, a hand-over, a pause."""


class JobPark(Platform):
    """The job a loop parks on: its key, the id of the call's request; the
    tool's own name for the work; and the budget hold it started under,
    when it spends. Ids and a name, never what the work said."""

    key: UUID
    handle: Stored = Field(min_length=1, max_length=MAX_NAME)
    hold_id: UUID | None = None


class Park(Platform):
    """What a parked loop waits on: its reason, what clears it, and when it
    tries again by itself. No retry time means only a person can unblock it,
    so a park only a person clears carries none. A park on a started job
    names it, and tries again at the job's deadline."""

    reason: ParkReason
    unlock: Stored = Field(min_length=1, max_length=MAX_NAME)
    """What clears the park, named as a kind or an id (`approval`, a job's
    id), never as content."""
    retry_at: datetime | None = None
    job: JobPark | None = None

    @model_validator(mode="after")
    def _a_person_sets_no_clock(self) -> Self:
        if self.retry_at is not None and self.reason in PERSON_ONLY:
            raise ValueError(f"a {self.reason.value} park is cleared by a person, never by a time")
        if self.job is not None and (self.reason is not ParkReason.JOB or self.retry_at is None):
            raise ValueError("only a job park names a job, and it tries again at its deadline")
        return self


class InputHeader(Platform):
    """A `message` or an `event`. `waking` is set when the input arrives, by
    the adopter's routing: a waking input starts a loop on an idle session.
    Left None, it takes its type's default when the step is built
    (`steps.types.step.WAKES_BY_DEFAULT`): a principal's message wakes, and
    an event from outside does not.

    `principal` is the authority it arrives on: the person or program that
    wrote a message, the principal a child's spawn ran under for its
    parent's message, or the one the adopter's routing delivers an event
    under. `agent` names the agent that wrote it, when its actor is an
    agent. `untrusted` carries that agent's session's mark to the session
    it reaches, and is set on any input that carries a file, which is data
    whoever attached it (`steps.types.step.Step`)."""

    kind: Literal["input"] = "input"
    waking: bool | None = None
    principal: Principal
    agent: AgentRef | None = None
    untrusted: bool = False


class DecidedCall(Platform):
    """The tool call a person's approve or deny decides: its tool and its
    input's hash, as its request recorded them, and who decided, with the
    role they held in the tenant then. The history writes both in the name
    of the context that appends the decision (`attribution.rules.decided_by`),
    and a decision counts only while the tenant's policy lets that role
    decide the call's class (`tools.rules.decides`). The control step
    references that request. An approval holds until `expires_at`; a
    denial holds for good and carries none."""

    tool: Stored = Field(min_length=1, max_length=MAX_NAME)
    input_hash: Stored = Field(min_length=1, max_length=MAX_NAME)
    decided_by: UUID
    role: Role
    expires_at: datetime | None = None


class ControlHeader(Platform):
    """`call` is the decided call of an approve or a deny, and of nothing
    else."""

    kind: Literal["control"] = "control"
    command: ControlCommand
    call: DecidedCall | None = None

    @model_validator(mode="after")
    def _a_decision_names_its_call(self) -> Self:
        decides = self.command in (ControlCommand.APPROVE, ControlCommand.DENY)
        if decides != (self.call is not None):
            raise ValueError(
                "an approve or a deny names the call it decides, and nothing else does"
            )
        if self.call is not None and (
            (self.command is ControlCommand.APPROVE) != (self.call.expires_at is not None)
        ):
            raise ValueError("an approval expires, and a denial does not")
        return self


class ModelRequestHeader(Platform):
    """One call of one model role. Its content is empty: it references the
    inputs it delivered, and records the window it read by reference: the
    fill it was sized for and the fill set's version that named it, its left
    edge (the first step it reads verbatim), and the summary it reads before
    them. `prompt_hash` is a hash of the rendered prompt keyed by the
    session, so a cache regression is a query and a replay is checked by it.
    `speaker` is the principal behind the latest principal-authored input
    the model has received, this request's included: the person a delegated
    call it leads to runs under. `spender` pays for the call. `hold_id` is
    the budget hold the call's worst case was reserved by before the
    request was written, so a run that finds the request unanswered after
    a crash settles that hold rather than leaving it held."""

    kind: Literal["model_request"] = "model_request"
    role: Stored = Field(min_length=1, max_length=MAX_NAME)
    spender: Principal
    speaker: Principal | None = None
    fill: Stored = Field(min_length=1, max_length=MAX_NAME)  # provider/model
    fill_set_version: int = Field(ge=1)
    left_edge: int = Field(ge=1)
    summary_id: UUID | None = None
    prompt_hash: Stored = Field(min_length=1, max_length=MAX_NAME)
    hold_id: UUID | None = None


class ModelResponseHeader(Platform):
    """A response saved once, whole, when its stream ends. `truncated` marks
    a stream that broke, or a reply cut by its output bound, holding what
    arrived; `abandoned` marks the close of a request that never got its
    response, written by the run that saw its call fail or by a new run
    that found it open. `stop_reason` is why the provider stopped, when it
    said."""

    kind: Literal["model_response"] = "model_response"
    truncated: bool = False
    abandoned: bool = False
    usage: Usage | None = None  # what the provider reported the call used
    stop_reason: StopReason | None = None


class ToolRequestHeader(Platform):
    """One tool call, referencing the tool-use block of the response that
    asked for it (`tool_use_id`, with that response among the step's
    `refs`) and carrying its input's hash, never its input, and the class
    of power its tool exercises, which decides who may approve it. The
    agent acts (`agent`); the call runs under `principal`, whom `authority`
    chose and the adopter's transition answered for on this call."""

    kind: Literal["tool_request"] = "tool_request"
    tool: Stored = Field(min_length=1, max_length=MAX_NAME)
    tool_use_id: Stored = Field(min_length=1, max_length=MAX_NAME)
    input_hash: Stored = Field(min_length=1, max_length=MAX_NAME)
    principal: Principal
    authority: AuthorityMode
    agent: AgentRef
    authorization_class: Stored = Field(min_length=1, max_length=MAX_NAME)


class ArtifactRef(Platform):
    """The handle of a tool result kept whole as an artifact, outside the
    step: its id, and how many characters it holds. The step keeps the
    result's head and tail; a read tool pages through the rest by the id."""

    id: UUID
    characters: int = Field(gt=0)


class AcceptedResult(Platform):
    """A result the kind's result gate accepted: the outcome its loop ends
    with, and whether a gate that knows the evidence judged it."""

    outcome: LoopOutcome
    verified: bool = False


class ToolResponseHeader(Platform):
    """A result, or the class of the failure the call met. `interrupted`
    marks a call stopped before it answered, its outcome unknown, so the
    model verifies before it retries. `artifact` is the handle of a result
    above the size bound, whose head and tail are the first two parts of the
    step's result. `accepted` is the verdict on a result submitted through
    the kind's result tool, kept in the history so the loop ends on it even
    after a park or a lost run."""

    kind: Literal["tool_response"] = "tool_response"
    failure: ToolFailure | None = None
    artifact: ArtifactRef | None = None
    accepted: AcceptedResult | None = None

    @model_validator(mode="after")
    def _a_failure_accepts_nothing(self) -> Self:
        if self.accepted is not None and self.failure is not None:
            raise ValueError("an accepted result is no failure")
        return self

    @property
    def interrupted(self) -> bool:
        return self.failure is ToolFailure.INTERRUPTED


class SummaryHeader(Platform):
    """The range of the history a summary stands for when a model reads it."""

    kind: Literal["summary"] = "summary"
    first_seq: int = Field(ge=1)
    last_seq: int = Field(ge=1)

    @model_validator(mode="after")
    def _a_range(self) -> Self:
        if self.last_seq < self.first_seq:
            raise ValueError("a summary's range ends before it starts")
        return self


class ParkedHeader(Platform):
    """A `parked` step writes no outcome: the loop is suspended, not ended."""

    kind: Literal["parked"] = "parked"
    park: Park


class LoopEndedHeader(Platform):
    kind: Literal["loop_ended"] = "loop_ended"
    outcome: LoopOutcome


class SwitchedHeader(Platform):
    """A switch, explicit: the fill set's new version and both fills it
    names. A switch is never silent, so no fill changes without one."""

    kind: Literal["switched"] = "switched"
    fills: FillSwitch


class MarkHeader(Platform):
    """A lifecycle mark with nothing of its own to say: `resumed`,
    `environment_changed`."""

    kind: Literal["mark"] = "mark"


StepHeader = Annotated[
    InputHeader
    | ControlHeader
    | ModelRequestHeader
    | ModelResponseHeader
    | ToolRequestHeader
    | ToolResponseHeader
    | SummaryHeader
    | ParkedHeader
    | LoopEndedHeader
    | SwitchedHeader
    | MarkHeader,
    Field(discriminator="kind"),
]
