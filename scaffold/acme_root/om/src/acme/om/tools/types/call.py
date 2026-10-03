"""What the engine learns of one call on its way to a response: whether it
may run, what a person decided about it, and, for a job, the handle it
waits on."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from acme.om.attribution.types.authority import CallAuthority
from acme.om.base import Platform
from acme.om.steps.types.content import MAX_NAME
from acme.om.steps.types.header import ToolFailure
from acme.om.steps.types.step import Step
from acme.om.tools.types.policy import Decision


class Verdict(StrEnum):
    """A person's decision on one exact call, as the history holds it."""

    APPROVED = "approved"  # an approval that has not expired
    DENIED = "denied"
    EXPIRED = "expired"  # approved once and expired since: asked again
    PENDING = "pending"  # nobody has decided


class GateOutcome(StrEnum):
    RUN = "run"  # the call may run now
    ASK = "ask"  # it waits for a person: the loop parks
    REFUSE = "refuse"  # it never runs: `response` answers it


class Gate(Platform):
    """Where a call stands before it runs. A refused call carries the
    response that answers it; `decision` is policy's, when policy was asked,
    and `authority` attribution's answer for the call then: the principal it
    runs under and that principal's live context, which a call that runs
    runs under."""

    outcome: GateOutcome
    decision: Decision | None = None
    response: Step | None = None
    authority: CallAuthority | None = None


class JobStarted(Platform):
    """The output of a job tool's run: the tool's own name for the work, a
    name the loop's park keeps."""

    handle: str = Field(min_length=1, max_length=MAX_NAME)


class JobHandle(Platform):
    """Work a `job` tool started, under the key of its request: the loop
    parks on it, and its completion arrives as an event."""

    tool: str
    key: UUID  # the id of the tool request
    handle: str = Field(min_length=1, max_length=MAX_NAME)  # the tool's own name for the work
    deadline: datetime  # never later than the tree's


class JobNotStarted(Platform):
    """A job whose start was refused before any work began: its input,
    refused before its tool ran, or the tool's own `JobRefused`. `response`
    answers its call. Nothing ran, so its hold is released; a start that
    failed any other way may have started the work."""

    response: Step


MAX_REPORT = 1_000_000
"""The most a job's completion may say, in characters. What the model reads
of it is bounded again when its call is answered."""


class JobCompletion(Platform):
    """How a job ended, as its completion event says. It names the job by
    its key and the tool's handle, both of which must be the job a loop
    waits on; it carries the job's result as text, the class of its failure
    when it failed, and what it cost at reference cost when its runner
    reports that. Outside input: the engine checks it before it is kept,
    and again before the call is answered from it."""

    key: UUID
    handle: str = Field(min_length=1, max_length=MAX_NAME)
    text: str = Field(default="", max_length=MAX_REPORT)
    failure: ToolFailure | None = None
    cost_micros: int | None = Field(default=None, ge=0)
