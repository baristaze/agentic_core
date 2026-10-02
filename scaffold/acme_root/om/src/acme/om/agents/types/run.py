"""What one run of a session's loop comes to."""

from enum import StrEnum
from uuid import UUID

from pydantic import Field

from acme.om.base import Platform
from acme.om.steps.types.header import LoopOutcome, Park


class RunEnd(StrEnum):
    """How a run stopped. Only `ended` closes the loop; the others leave it
    for a later run."""

    ENDED = "ended"  # the loop ended with an outcome
    PARKED = "parked"  # the loop waits on an unlock
    YIELDED = "yielded"  # the run's time ran out; the next run continues the loop
    STALE = "stale"  # a later run took the session's claim; this one wrote nothing more
    IDLE = "idle"  # the session had no loop to run


class LoopRun(Platform):
    """A run, as its caller learns of it: the epoch it held, the loop it
    ran, how it stopped, and the outcome or the park it stopped on."""

    session_id: UUID
    epoch: int = Field(ge=0)
    loop_id: UUID | None = None
    end: RunEnd
    outcome: LoopOutcome | None = None
    park: Park | None = None
