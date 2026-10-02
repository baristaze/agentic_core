"""A step's header: its shape, typed per type, readable while its content is
sealed. Each header names its `kind`, and a step holds the one its type
fixes (`steps.types.step.HEADER_KINDS`). A header carries ids, names,
counts, and flags, never what a person typed or a tool returned."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from acme.om.base import Platform
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


class Park(Platform):
    """What a parked loop waits on: its reason, what clears it, and when it
    tries again by itself. No retry time means only a person can unblock it."""

    reason: ParkReason
    unlock: Stored = Field(min_length=1, max_length=MAX_NAME)
    """What clears the park, named as a kind or an id (`approval`, a job's
    id), never as content."""
    retry_at: datetime | None = None


class InputHeader(Platform):
    """A `message` or an `event`. `waking` is set when the input arrives, by
    the adopter's routing: a waking input starts a loop on an idle session.
    Left None, it takes its type's default when the step is built
    (`steps.types.step.WAKES_BY_DEFAULT`): a principal's message wakes, and
    an event from outside does not."""

    kind: Literal["input"] = "input"
    waking: bool | None = None


class DecidedCall(Platform):
    """The tool call a person's approve or deny decides: its tool and its
    input's hash, as its request recorded them, and who decided. The control
    step references that request. An approval holds until `expires_at`; a
    denial holds for good and carries none."""

    tool: Stored = Field(min_length=1, max_length=MAX_NAME)
    input_hash: Stored = Field(min_length=1, max_length=MAX_NAME)
    decided_by: UUID
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
    steps it carried."""

    kind: Literal["model_request"] = "model_request"
    role: Stored = Field(min_length=1, max_length=MAX_NAME)


class ModelResponseHeader(Platform):
    """A response saved once, whole, when its stream ends. `truncated` marks
    a stream that broke, holding what arrived; `abandoned` marks the close a
    new run writes for a request that never got its response."""

    kind: Literal["model_response"] = "model_response"
    truncated: bool = False
    abandoned: bool = False


class ToolRequestHeader(Platform):
    """One tool call, referencing the tool-use block of the response that
    asked for it (`tool_use_id`, with that response among the step's
    `refs`) and carrying its input's hash, never its input, and the class
    of power its tool exercises, which decides who may approve it."""

    kind: Literal["tool_request"] = "tool_request"
    tool: Stored = Field(min_length=1, max_length=MAX_NAME)
    tool_use_id: Stored = Field(min_length=1, max_length=MAX_NAME)
    input_hash: Stored = Field(min_length=1, max_length=MAX_NAME)
    authorization_class: Stored = Field(min_length=1, max_length=MAX_NAME)


class ToolResponseHeader(Platform):
    """A result, or the class of the failure the call met. `interrupted`
    marks a call stopped before it answered, its outcome unknown, so the
    model verifies before it retries."""

    kind: Literal["tool_response"] = "tool_response"
    failure: ToolFailure | None = None

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
