"""The handler of LOOP: one run of a session's loop, under the item's
lease.

The run takes the session's next writer epoch before it reads anything, so
a run that lost its claim writes nothing more, and it settles a lost run's
open calls by their effect (`LoopManagerInterface.run`). The item's lease
fences the queue row; the epoch fences the history. A run whose time is up
hands its item back at once, with no attempt spent, and the next claim
goes on with the loop. A session that is gone has nothing to run, and
completes its item; any other thing not found, such as a kind this
process does not declare, fails it, so it is retried and, past its
attempts, dead-lettered where an operator requeues it. Every other end
completes the item: an ended or parked loop, a session another run
holds, and one with nothing to run."""

import logging
from datetime import timedelta
from typing import ClassVar

from acme.om.agent_sessions import AgentSessionsManagerInterface
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

    REQUIRES: ClassVar[tuple[Permission, ...]] = (Permission.READ, Permission.WRITE)
    """`run` appends steps and projects the status, and a run that found
    nothing reads the session; each tool call asks its principal's own
    permissions again."""

    def __init__(self, loop: LoopManagerInterface, sessions: AgentSessionsManagerInterface) -> None:
        self._loop = loop
        self._sessions = sessions

    async def handle(self, ctx: TenantContext, item: WorkItem) -> None:
        try:
            run = await self._loop.run(ctx, item.target_id)
        except NotFound:
            if not await self._gone(ctx, item):
                raise
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

    async def _gone(self, ctx: TenantContext, item: WorkItem) -> bool:
        """Whether the session itself is gone: what the run did not find is
        the session only when a read of it finds nothing either."""
        try:
            await self._sessions.get_session(ctx, item.target_id)
        except NotFound:
            return True
        return False
