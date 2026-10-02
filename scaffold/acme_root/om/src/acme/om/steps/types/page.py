"""What a read of the history answers with, and the session's cursor."""

from acme.om.base import Platform
from acme.om.steps.types.step import Step


class StepPage(Platform):
    """Steps in `seq` order, and whether more follow. The manager asks
    storage for one step more than the page and keeps it out."""

    items: tuple[Step, ...]
    has_more: bool


class StepCursor(Platform):
    """A session's cursor row: `head` is the last `seq` an append assigned,
    and `epoch` the writer epoch of the run that holds the session. Both are
    0 before the first append and the first run."""

    head: int = 0
    epoch: int = 0
