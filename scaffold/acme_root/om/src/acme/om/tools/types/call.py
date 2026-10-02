"""What the engine learns of one call on its way to a response: whether it
may run, what a person decided about it, and, for a job, the handle it
waits on."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from acme.om.attribution.types.authority import CallAuthority
from acme.om.base import Platform
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
    """The output of a job tool's run: the tool's own name for the work."""

    handle: str = Field(min_length=1)


class JobHandle(Platform):
    """Work a `job` tool started, under the key of its request: the loop
    parks on it, and its completion arrives as an event."""

    tool: str
    key: UUID  # the id of the tool request
    handle: str = Field(min_length=1)  # the tool's own name for the work
    deadline: datetime  # never later than the tree's


class JobCompletion(Platform):
    """How a job ended, as its completion event says: its result as text,
    and the class of its failure when it failed."""

    key: UUID
    text: str = ""
    failure: ToolFailure | None = None
