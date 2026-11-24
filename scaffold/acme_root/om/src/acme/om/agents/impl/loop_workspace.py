"""The run's workspace: its spec checked before the loop's first model
call, and its instance prepared at the first call that reaches it, so no
model call waits for a workspace. A kind that prepares early starts the
prepare beside the run's first model call instead, and that call takes
it (ADR 1048). A spec the provider can never meet ends the loop before
anything is spent; a workspace that cannot be prepared yet parks the
loop with its call open, and the run that takes the loop up again gates
and runs that call once (ADR 1043)."""

import asyncio
import logging
from collections.abc import Sequence
from uuid import UUID

from acme.infra.agent_workspaces import (
    AgentWorkspace,
    AgentWorkspaceLost,
    Durability,
    IsolationRefused,
)
from acme.infra.exceptions import InfraException
from acme.infra.observability import WORKSPACE_PREPARES_UNUSED
from acme.om.agent_steps.types.agent_step import AgentStep, AgentStepType
from acme.om.agent_steps.types.header import (
    LoopOutcome,
    Park,
    ParkReason,
    ToolFailure,
    ToolRequestHeader,
    ToolResponseHeader,
)
from acme.om.agents import loop_rules as rules
from acme.om.agents.impl.loop_core import EarlyPrepare, LoopCore, RunState
from acme.om.agents.types.run import LoopRun
from acme.om.base import new_id
from acme.om.tools.rules import ISOLATION_REFUSED, named_snapshot, response_id

log = logging.getLogger(__name__)

AGENT_WORKSPACE_UNLOCK = "agent_workspace"
LOST_SNAPSHOT = "the snapshot the workspace starts from is gone, or no longer matches what was kept"
LOST_SOURCE = "what the workspace is rebuilt from is gone, or cannot be brought in"
"""The causes a park on a lost workspace names, the engine's own words: the
provider's message may hold what a command wrote, and reaches the log
alone."""
REFUSED = "the session's workspace was refused, and the loop ended"
"""What a call still open reads when a refused workspace ends its loop,
with the refusal's cause when it names one."""


def lost_agent_workspace(park: Park) -> bool:
    """Whether a park waits for a person on a workspace that was lost."""
    return park.reason is ParkReason.PERSON and park.unlock == AGENT_WORKSPACE_UNLOCK


class LoopWorkspace(LoopCore):
    """The run's workspace: checked up front, prepared at the first call
    that reaches it or beside the run's first model call, and how the run
    stops when it cannot be."""

    async def _check(self, run: RunState) -> LoopRun | None:
        """The session's spec, held to what the provider can ever meet before
        the run's first model call, with nothing made: a spec it never meets
        is refused whole, and no call is spent on a loop that cannot run.
        None when it can be met; else how the run stopped."""
        try:
            await self._tools.check_agent_workspace(run.ctx, run.kind.isolation)
        except InfraException as refused:
            # Before the calls a lost run left open are settled.
            return await self._unprepared(run, refused, restoring=False, settled=run.resumed)
        return None

    async def _workspace(
        self, run: RunState, history: Sequence[AgentStep], request: AgentStep, *, fresh: bool
    ) -> AgentWorkspace | LoopRun:
        """The workspace a call is gated and run in. A call that does not
        reach it is given none, which every transport refuses, loudly. One
        that does is given the run's, prepared now when it is the run's
        first: only that call waits for it. A workspace that cannot be
        prepared stops the run with the call open, its park or its end
        naming the refusal's cause. `fresh` when no run may have started the
        calls still open; a park on the workspace is otherwise unsettled."""
        header = request.header
        assert isinstance(header, ToolRequestHeader)
        if not header.workspace:
            return AgentWorkspace.absent(run.ctx.org_id, run.agent_session_id)
        if run.workspace is None:
            stopped = await self._prepare(run, history, settled=fresh)
            if stopped is not None:
                return stopped
        assert run.workspace is not None
        return run.workspace

    async def _prepare(
        self, run: RunState, history: Sequence[AgentStep], *, settled: bool
    ) -> LoopRun | None:
        """The run's workspace, from what the history names: a restore a
        principal or a fork asked for wins; a workspace kept by snapshots
        otherwise starts from its latest, unless something touched it since,
        and then it stands newer than any snapshot. Once it is prepared,
        the steps the hooks append about it are written. None then; else
        how the run stopped."""
        history = await self._history(run.ctx, run.agent_session_id, history, own=run.own)
        start = self._starts_from(run, history, settled=settled)
        stopped = await self._prepare_from(run, start, settled=settled)
        if stopped is not None:
            return stopped
        # What a layer tells the model of the workspace, before the call
        # that reached it is gated: the model reads it with that call's
        # answer.
        assert run.began is not None and run.workspace is not None
        steps = await self._hooks.workspace_prepared(run.ctx, run.began, run.workspace)
        if steps:
            await self._append(run, list(steps))
        return None

    def _starts_from(
        self, run: RunState, history: Sequence[AgentStep], *, settled: bool
    ) -> AgentStep | None:
        """The step the run's workspace starts from, as `history` names it:
        a restore a principal or a fork asked for wins; a workspace kept by
        snapshots otherwise starts from its latest, unless something touched
        it since, and then it stands newer than any snapshot. None when it
        starts from no snapshot."""
        start = rules.pending_restore(history)
        if start is None and run.kind.isolation.durability is Durability.SNAPSHOT:
            start = rules.kept_snapshot(self._read(run, history), settled=settled)
        return start

    def _read(self, run: RunState, history: Sequence[AgentStep]) -> list[AgentStep]:
        """The history as it tells what touched the workspace. A notice the
        run's hooks wrote before its workspace was prepared touched nothing
        of it. Anything else the run wrote is read as any run's would be:
        its answer to a call an earlier run made tells whether that call ran
        after the snapshot."""
        return [
            step
            for step in history
            if not (step.seq in run.own and step.type is AgentStepType.ENVIRONMENT_CHANGED)
        ]

    def _prepare_early(self, run: RunState, history: Sequence[AgentStep]) -> None:
        """The run's prepare, started as each model call goes out, once the
        outage mark, the gate, and the key let it: only the run's first
        starts one, when its kind prepares early and the run holds no
        workspace, from what `history` names. The first call that reaches
        the workspace takes it (`_taken`), and the run's end lets go of one
        no call took (`_let_go_early`). As a model call goes out no call of
        the loop is open, so the start is read as settled. A workspace kept
        by snapshots that something touched since its latest may stand live,
        holding what no snapshot holds: it is never prepared early, so no
        end lets that instance go, and its first call finds it as it
        stands."""
        first, run.called = not run.called, True
        if not first or not run.kind.prepares_early or run.workspace is not None:
            return
        start = self._starts_from(run, history, settled=True)
        snapshots = run.kind.isolation.durability is Durability.SNAPSHOT
        if start is None and snapshots and not rules.untouched(self._read(run, history)):
            return
        restore = None if start is None else named_snapshot(start)
        prepare = self._tools.prepare_agent_workspace(
            run.ctx, run.agent_session_id, run.kind.isolation, restore=restore
        )
        run.early = EarlyPrepare(task=asyncio.create_task(prepare), start=start)

    async def _taken(self, run: RunState, start: AgentStep | None) -> AgentWorkspace | None:
        """What the run's early prepare made, taken by the first call that
        reaches the workspace, which waits for it while it runs. None when
        the run started none, when it failed, or when it started from
        another step than `start`, such as before a restore a principal
        asked for since: its instance goes, and the call then prepares as
        any first call does. A failure reaches the log, and the call's own
        prepare finds what it started, or meets it again while it stands."""
        early = run.early
        if early is None:
            return None
        run.early = None
        try:
            workspace = await early.task
        except Exception as failed:
            log.warning("session %s: its early prepare failed: %s", run.agent_session_id, failed)
            return None
        if _id(early.start) != _id(start):
            await self._let_go(run, workspace)
            return None
        return workspace

    async def _let_go_early(self, run: RunState, *, release: bool = True) -> None:
        """An early prepare no call took, at the run's end: before its end,
        park, or yield is written (`_keep`), or once a run that raised
        stops. The run waits for it, so no prepare outlives its run; a run
        itself cancelled cancels it. The instance it made is let go as a
        run's end lets one go, with no snapshot, since no call touched it,
        and is counted by kind. `release` is False when the run lost its
        claim: what it made is the claim holder's."""
        early = run.early
        if early is None:
            return
        run.early = None
        task = early.task
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            task.cancel()
        try:
            await asyncio.wait((task,))
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.wait((task,))
            raise
        if task.cancelled():
            return
        failed = task.exception()
        if failed is not None:
            log.warning("session %s: its early prepare failed: %s", run.agent_session_id, failed)
            return
        if not release:
            return
        WORKSPACE_PREPARES_UNUSED.labels(kind=run.kind.name).inc()
        await self._let_go(run, task.result())

    async def _let_go(self, run: RunState, workspace: AgentWorkspace) -> None:
        """An instance no call reached let go, its files kept, as a run's end
        lets one go. A release that fails is logged."""
        try:
            await self._tools.release_agent_workspace(run.ctx, workspace)
        except Exception:
            log.exception("session %s: the workspace was not released", run.agent_session_id)

    async def _prepare_from(
        self, run: RunState, start: AgentStep | None, *, settled: bool
    ) -> LoopRun | None:
        """The run's workspace, a weaker one than the spec never made. It
        starts from the snapshot `start` names, when there is one, and is
        what the run's early prepare made when that started from the same.
        None once it is prepared; else how the run stopped.

        A restore that cannot load parks the loop for a person. Once the
        person lets it go on (`run.unlocked`) and it still cannot load, the
        loop goes on from the workspace as it stands, and a notice that
        references `start` records the restore lost, so it is never tried
        again and the model reads why."""
        restore = None if start is None else named_snapshot(start)
        try:
            workspace = await self._taken(run, start)
            if workspace is None:
                workspace = await self._tools.prepare_agent_workspace(
                    run.ctx, run.agent_session_id, run.kind.isolation, restore=restore
                )
            run.workspace = workspace
        except InfraException as refused:
            if start is not None and run.unlocked and refused.code == AgentWorkspaceLost.code:
                log.warning(
                    "session %s goes on without its snapshot: %s", run.agent_session_id, refused
                )
                lost = rules.lost_step(
                    new_id(), self._clock(), run.agent_session_id, run.loop_id, start
                )
                await self._append(run, [lost])
                return await self._prepare_from(run, None, settled=settled)
            return await self._unprepared(
                run, refused, restoring=start is not None, settled=settled
            )
        if start is not None:
            # The record that a restore was done, and the model's notice: it
            # is delivered with the next model request. A run lost before it
            # is written restores again, to the same snapshot.
            done = rules.restore_notice(
                new_id(), self._clock(), run.agent_session_id, run.loop_id, start
            )
            if done is not None:
                await self._append(run, [done])
        return None

    async def _unprepared(
        self, run: RunState, refused: InfraException, *, restoring: bool, settled: bool
    ) -> LoopRun:
        """How a run stops when its workspace cannot be had: its end or its
        park names the refusal's cause, so a person reads why without the
        log. `restoring` when the workspace was to start from a snapshot;
        `settled` when no run may have started a call still open, else the
        park is unsettled."""
        cause = refused.cause if isinstance(refused, IsolationRefused) else None
        if isinstance(refused, IsolationRefused) and refused.clears:
            # No host can give it the workspace yet: no weaker one, and the
            # call that needs it waits. It waits on the resource and asks
            # again.
            log.warning("session %s waits for a workspace: %s", run.agent_session_id, refused)
            retry_at = self._clock() + self._options.agent_workspace_wait
            park = Park(
                reason=ParkReason.LEASABLE_RESOURCE,
                unlock=AGENT_WORKSPACE_UNLOCK,
                retry_at=retry_at,
                unsettled=not settled,
                cause=cause,
            )
            return await self._park(run, park)
        if refused.code == ISOLATION_REFUSED:
            # A provider that cannot meet the spec, or a base whose setup
            # failed, refuses it whole: no weaker workspace, ever. The loop
            # ends with no call left open, so the session's next input
            # starts a loop whose history renders.
            log.warning("session %s has no workspace: %s", run.agent_session_id, refused)
            await self._refuse_open(run, cause)
            return await self._end(run, LoopOutcome.ERRORED, cause=cause)
        if refused.code != AgentWorkspaceLost.code:
            raise refused
        # What the workspace is rebuilt from is gone: nothing restarts
        # from scratch in its stead, and a person says what comes next.
        # The loop parks with everything it reached kept.
        log.error("session %s lost its workspace: %s", run.agent_session_id, refused)
        park = Park(
            reason=ParkReason.PERSON,
            unlock=AGENT_WORKSPACE_UNLOCK,
            unsettled=not settled,
            cause=LOST_SNAPSHOT if restoring else LOST_SOURCE,
        )
        return await self._park(run, park)

    async def _refuse_open(self, run: RunState, cause: str | None) -> None:
        """Every call of the loop's latest response still open, answered
        before a refused workspace ends the loop, each naming `cause`: one
        no run started as never run, marked `unstarted` since it touched
        nothing, and one a lost run may have started with its outcome
        unknown. A job a run started is the end's to stop and answer."""
        history = await self._history(run.ctx, run.agent_session_id)
        response = rules.latest_response(history, run.loop_id)
        if response is None:
            return
        jobs = rules.started_jobs(history, run.loop_id)
        calls = [
            (use, request)
            for use, request in rules.open_calls(history, response)
            if request is None or request.id not in jobs
        ]
        if not calls:
            return
        why = REFUSED if cause is None else f"{REFUSED}: {cause}"
        answers: list[AgentStep] = []
        for _, request in await self._requests(run, response, calls):
            if self._never_ran(run, request):
                answer = self._stopped(request, f"not run: {why}", ToolFailure.PERMANENT)
                header = ToolResponseHeader(failure=ToolFailure.PERMANENT, unstarted=True)
                answer = answer.model_copy(update={"header": header})
            else:
                answer = self._lost(request, why)
            answers.append(answer.model_copy(update={"id": response_id(request, run.epoch)}))
        await self._append(run, answers)


def _id(step: AgentStep | None) -> UUID | None:
    return None if step is None else step.id
