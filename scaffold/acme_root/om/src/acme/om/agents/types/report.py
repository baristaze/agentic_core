"""What a child tells its parent of one of its loops: how the loop stands,
the outcome it ended with or the park on a person it waits on; the result
its gate accepted; the last thing its model said; and whether its parent
was the one that cancelled it. The loop reads all of it from the child's
history, and its parent reads it as data."""

from typing import Self
from uuid import UUID

from pydantic import model_validator

from acme.om.base import Platform
from acme.om.steps.types.header import AcceptedResult, LoopOutcome, Park


class Report(Platform):
    """One status change of a child's loop `loop_id`: it ended with
    `outcome`, or it parked on `park`. `accepted` is the verdict its result
    gate gave, when it ended on one. `answer` is the text of the loop's
    latest response that said something, or None. `cancelled_by_parent` is
    set when the cancel that ended it came down from its parent."""

    loop_id: UUID
    outcome: LoopOutcome | None = None
    park: Park | None = None
    accepted: AcceptedResult | None = None
    answer: str | None = None
    cancelled_by_parent: bool = False

    @model_validator(mode="after")
    def _one_status(self) -> Self:
        if (self.outcome is None) == (self.park is None):
            raise ValueError("a report names how its loop ended, or the park it waits on")
        if self.accepted is not None and self.outcome is None:
            raise ValueError("only a loop that ended reports an accepted result")
        if self.cancelled_by_parent and self.outcome is not LoopOutcome.CANCELLED:
            raise ValueError("only a cancelled loop was cancelled by its parent")
        return self
