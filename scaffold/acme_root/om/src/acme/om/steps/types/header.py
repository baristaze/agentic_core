"""A step's header: its shape, typed per type, readable while its content is
sealed. Each header names its `kind`, and a step holds the one its type
fixes (`steps.types.step.HEADER_KINDS`). A header carries ids, names,
counts, and flags, never what a person typed or a tool returned."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from acme.om.base import Platform
from acme.om.steps.types.content import MAX_NAME


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


class LoopOutcome(StrEnum):
    """The five ways a loop ends. A park is none of them."""

    SUCCEEDED = "succeeded"  # the kind's done rule was met and its result gate accepted
    FAILED = "failed"  # a conclusion, with evidence, that the objective cannot be met
    INCONCLUSIVE = "inconclusive"  # stopped without a conclusion
    CANCELLED = "cancelled"  # a principal cancelled it
    ERRORED = "errored"  # an error no park can clear


PERSON_ONLY = frozenset({ParkReason.PERSON, ParkReason.HANDOVER, ParkReason.PAUSE})
"""The reasons only a person clears: a question, a hand-over, a pause."""


class Park(Platform):
    """What a parked loop waits on: its reason, what clears it, and when it
    tries again by itself. No retry time means only a person can unblock it,
    so a park only a person clears carries none."""

    reason: ParkReason
    unlock: str = Field(min_length=1, max_length=MAX_NAME)
    """What clears the park, named as a kind or an id (`approval`, a job's
    id), never as content."""
    retry_at: datetime | None = None

    @model_validator(mode="after")
    def _a_person_sets_no_clock(self) -> Self:
        if self.retry_at is not None and self.reason in PERSON_ONLY:
            raise ValueError(f"a {self.reason.value} park is cleared by a person, never by a time")
        return self


class InputHeader(Platform):
    """A `message` or an `event`. `waking` is set when the input arrives, by
    the adopter's routing: a waking input starts a loop on an idle session."""

    kind: Literal["input"] = "input"
    waking: bool = True


class ControlHeader(Platform):
    kind: Literal["control"] = "control"
    command: ControlCommand


class ModelRequestHeader(Platform):
    """One call of one model role. Its content is empty: it references the
    steps it carried."""

    kind: Literal["model_request"] = "model_request"
    role: str = Field(min_length=1, max_length=MAX_NAME)


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
    `refs`) and carrying its input's hash, never its input."""

    kind: Literal["tool_request"] = "tool_request"
    tool: str = Field(min_length=1, max_length=MAX_NAME)
    tool_use_id: str = Field(min_length=1, max_length=MAX_NAME)
    input_hash: str = Field(min_length=1, max_length=MAX_NAME)


class ToolResponseHeader(Platform):
    """`interrupted` marks a call stopped before it answered, its outcome
    unknown, so the model verifies before it retries."""

    kind: Literal["tool_response"] = "tool_response"
    interrupted: bool = False


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


class MarkHeader(Platform):
    """A lifecycle mark with nothing of its own to say: `resumed`,
    `switched`, `environment_changed`."""

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
    | MarkHeader,
    Field(discriminator="kind"),
]
