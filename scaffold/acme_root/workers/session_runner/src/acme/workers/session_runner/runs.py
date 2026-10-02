"""The handler of LOOP: one run of a session's loop, under the item's
lease.

The run takes the session's next writer epoch before it reads anything, so
a run that lost its claim writes nothing more, and it settles a lost run's
open calls by their effect (`LoopManagerInterface.run`). The item's lease
fences the queue row; the epoch fences the history. A run whose time is up
hands its item back at once, with no attempt spent, and the next claim
goes on with the loop. Every other end completes the item: an ended or
parked loop, a session another run holds, and one with nothing to run."""

import logging
from datetime import timedelta
from typing import ClassVar

from acme.om.agents import LoopManagerInterface
from acme.om.agents.types.run import RunEnd
from acme.om.context import Permission, TenantContext
from acme.om.exceptions import NotFound
from acme.om.work.types.handler import WorkHandlerInterface, WorkParked
from acme.om.work.types.work_item import WorkItem

log = logging.getLogger(__name__)


class LoopHandlerImpl(WorkHandlerInterface):
    """Idempotent by the epoch: a second run of one item takes a new epoch,
    finds where the loop is from its history, and writes nothing that is
    there already. It holds nothing per item."""

    REQUIRES: ClassVar[tuple[Permission, ...]] = (Permission.WRITE,)
    """`run` appends steps and projects the status; each tool call asks its
    principal's own permissions again."""

    def __init__(self, loop: LoopManagerInterface) -> None:
        self._loop = loop

    async def handle(self, ctx: TenantContext, item: WorkItem) -> None:
        try:
            run = await self._loop.run(ctx, item.target_id)
        except NotFound:
            log.info("session %s is gone; loop %s has nothing to run", item.target_id, item.id)
            return
        log.info(
            "session %s in org %s: run at epoch %d %s%s",
            run.session_id,
            ctx.org_id,
            run.epoch,
            run.end.value,
            "" if run.outcome is None else f" {run.outcome.value}",
        )
        if run.end is RunEnd.YIELDED:
            raise WorkParked("the run's time is up; the next run goes on", timedelta(0))
