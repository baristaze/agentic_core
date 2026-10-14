"""The loop of a session: one run, from its session's status to its
end, its park, or its yield. A run reads its history once, prepares its
workspace, and drives each turn, the model's or the tools', reading only
what was added since."""

import logging
from collections.abc import Sequence
from uuid import UUID

from acme.infra.agent_workspaces import (
    AgentWorkspaceLost,
    Durability,
    IsolationRefused,
)
from acme.infra.exceptions import InfraException
from acme.integrations.model_providers.types import StopReason
from acme.om.agent_sessions.limits import Limit, Trip, tally_loop, tripped
from acme.om.agent_sessions.rules import QUESTION
from acme.om.agent_sessions.types.agent_session import AgentSession, AgentSessionStatus
from acme.om.agent_steps.types.agent_step import AgentStep, AgentStepType
from acme.om.agent_steps.types.header import (
    ControlCommand,
    LoopOutcome,
    ModelResponseHeader,
    Park,
    ParkReason,
)
from acme.om.agents import line_rules as lines
from acme.om.agents import loop_rules as rules
from acme.om.agents.hooks import (
    LoopStart,
    with_attached_toolsets,
)
from acme.om.agents.impl.loop_core import Heard, LoopOptions, RunState
from acme.om.agents.impl.loop_model_turn import LoopModelTurn
from acme.om.agents.impl.loop_parks import LoopParks
from acme.om.agents.impl.loop_recovery import LoopRecovery
from acme.om.agents.impl.loop_take_over import LoopTakeOver
from acme.om.agents.impl.loop_tool_turn import LoopToolTurn
from acme.om.agents.loop import LoopManagerInterface
from acme.om.agents.rules import after_turn
from acme.om.agents.types.result import Turn
from acme.om.agents.types.run import LoopRun, RunEnd
from acme.om.base import new_id
from acme.om.context import Permission, TenantContext
from acme.om.exceptions import (
    StaleWriter,
    UnpricedModel,
    UnresolvedRole,
)
from acme.om.language_models.types.fill import Eligibility
from acme.om.leases.types.request import WaiterKind
from acme.om.tools.rules import (
    ISOLATION_REFUSED,
    named_snapshot,
)
from acme.om.tools.types.policy import ToolPolicyLayer

log = logging.getLogger(__name__)

__all__ = ["LOST_SNAPSHOT", "LOST_SOURCE", "LoopManagerImpl", "LoopOptions", "lost_agent_workspace"]


AGENT_WORKSPACE_UNLOCK = "agent_workspace"
LOST_SNAPSHOT = "the snapshot the workspace starts from is gone, or no longer matches what was kept"
LOST_SOURCE = "what the workspace is rebuilt from is gone, or cannot be brought in"
"""The causes a park on a lost workspace names, the engine's own words: the
provider's message may hold what a command wrote, and reaches the log
alone."""


class LoopManagerImpl(
    LoopModelTurn, LoopToolTurn, LoopParks, LoopRecovery, LoopTakeOver, LoopManagerInterface
):
    """The loop: a run reads its session, prepares its workspace, and
    drives turns of the model and of the tools until the loop ends, parks,
    or yields. Each concern is a module of its own; the constructor and
    what they share are `LoopCore`'s."""

    async def run(self, ctx: TenantContext, agent_session_id: UUID) -> LoopRun:
        ctx.require(Permission.WRITE)
        # The epoch first, before the history is read: from here on a run
        # that held an earlier one writes nothing.
        epoch = await self._steps.begin_run(ctx, agent_session_id)
        try:
            return await self._run(ctx, agent_session_id, epoch)
        except StaleWriter:
            log.info("loop run of session %s at epoch %d lost its claim", agent_session_id, epoch)
            return LoopRun(agent_session_id=agent_session_id, epoch=epoch, end=RunEnd.STALE)

    async def _run(self, ctx: TenantContext, agent_session_id: UUID, epoch: int) -> LoopRun:
        session = await self._sessions.project_status(ctx, agent_session_id)
        idle = LoopRun(agent_session_id=agent_session_id, epoch=epoch, end=RunEnd.IDLE)
        if session.status in (AgentSessionStatus.IDLE, AgentSessionStatus.PARKED):
            return idle
        history = await self._history(ctx, agent_session_id)
        loop = rules.open_loop(history)
        resumed = False
        if loop is None:
            if session.pending_input is None:
                return idle
            loop_id = session.pending_input
            start = rules.start_seq(history, loop_id)
        else:
            loop_id, start = loop.loop_id, loop.start_seq
            if loop.park is not None:
                # Its unlock happened: a new run takes it up, and its gates
                # run again before its next call. A park the loop wrote
                # itself came at a safe point, so the calls it held back
                # never ran. A hand-over was written from outside, while a
                # call may have been running, and a park on the workspace
                # may come before its run settled a lost run's calls: those
                # are settled by effect.
                session = await self._sessions.resume(ctx, agent_session_id, epoch, loop_id)
                resumed = loop.park.reason is not ParkReason.HANDOVER and not loop.park.unsettled
        # The session's attached toolsets, once a run and the same each time,
        # folded into its kind before anything renders.
        attached = await self._hooks.attached_toolsets(ctx, session)
        kind = with_attached_toolsets(self._kinds.get(session.kind, session.kind_version), attached)
        registry = self._registry(session, kind, history, attached)
        tree = await self._agents.tree_of(ctx, agent_session_id)
        above = await self._policies_above(ctx, session)
        run = RunState(
            ctx=ctx,
            session=session,
            kind=kind,
            registry=registry,
            epoch=epoch,
            loop_id=loop_id,
            start_seq=start,
            started_at=self._clock(),
            resumed=resumed,
            deadline=tree.deadline,
            above=above or (),
            jobs={} if loop is None else rules.started_jobs(history, loop_id),
            attached=attached,
        )
        if above is None:
            # Its calls are decided under every kind above it, and one of
            # them went with its session's purge: no call is made under less.
            log.warning("session %s has a session above it past its purge", agent_session_id)
            return await self._end(run, LoopOutcome.ERRORED)
        try:
            await self._language_models.resolve_fill_set(
                ctx, agent_session_id, kind.roles, Eligibility(), kind.asks
            )
        except (UnresolvedRole, UnpricedModel) as refused:
            log.warning("session %s resolves no fill: %s", agent_session_id, refused.message)
            return await self._end(run, LoopOutcome.ERRORED)
        # A restore a principal or a fork asked for wins. A workspace kept by
        # snapshots otherwise starts from its latest, unless something touched
        # it since: then it stands newer than any snapshot.
        start = rules.pending_restore(history)
        if start is None and kind.isolation.durability is Durability.SNAPSHOT:
            start = rules.kept_snapshot(history)
        # A person let a loop go on that parked on its lost workspace.
        unlocked = loop is not None and loop.park is not None and lost_agent_workspace(loop.park)
        try:
            stopped = await self._prepare(run, start, unlocked=unlocked)
            if stopped is not None:
                return stopped
            stopped = await self._start(run, new_loop=loop is None)
            if stopped is not None:
                return stopped
            return await self._drive(run, history)
        except StaleWriter:
            # The claim is another run's, or a person's who took the
            # environment over: what it holds is theirs to release.
            run.workspace = None
            raise
        finally:
            await self._release(run)

    async def _start(self, run: RunState, *, new_loop: bool) -> LoopRun | None:
        """The steps the hooks append before the run's first model call, under
        its epoch. None once they are written; else how the run stopped: a
        fill the hooks refuse ends the loop, as the resolve before does."""
        assert run.workspace is not None
        start = LoopStart(
            agent_session=run.session,
            kind=run.kind,
            epoch=run.epoch,
            loop_id=run.loop_id,
            new_loop=new_loop,
            workspace=run.workspace,
        )
        try:
            steps = await self._hooks.before_first_call(run.ctx, start)
        except (UnresolvedRole, UnpricedModel) as refused:
            log.warning("session %s resolves no fill: %s", run.agent_session_id, refused.message)
            return await self._end(run, LoopOutcome.ERRORED)
        if steps:
            await self._steps.append_agent_steps(
                run.ctx, run.agent_session_id, run.epoch, list(steps)
            )
        return None

    async def _prepare(
        self, run: RunState, start: AgentStep | None, *, unlocked: bool
    ) -> LoopRun | None:
        """The run's workspace, before the first model call: a workspace
        weaker than the spec is never made, and nothing is spent on a loop
        that cannot run. It starts from the snapshot `start` names, when
        there is one. None once it is prepared; else how the run stopped.

        A restore that cannot load parks the loop for a person. Once the
        person lets it go on (`unlocked`) and it still cannot load, the loop
        goes on from the workspace as it stands, and a notice that
        references `start` records the restore lost, so it is never tried
        again and the model reads why."""
        restore = None if start is None else named_snapshot(start)
        try:
            run.workspace = await self._tools.prepare_agent_workspace(
                run.ctx, run.agent_session_id, run.kind.isolation, restore=restore
            )
        except InfraException as refused:
            if start is not None and unlocked and refused.code == AgentWorkspaceLost.code:
                log.warning(
                    "session %s goes on without its snapshot: %s", run.agent_session_id, refused
                )
                lost = rules.lost_step(
                    new_id(), self._clock(), run.agent_session_id, run.loop_id, start
                )
                await self._steps.append_agent_steps(
                    run.ctx, run.agent_session_id, run.epoch, [lost]
                )
                return await self._prepare(run, None, unlocked=unlocked)
            return await self._unprepared(run, refused, restoring=start is not None)
        if start is not None and start.type is AgentStepType.CONTROL:
            # The record that the restore was done, and the model's notice: it
            # is delivered with the next model request. A run lost before it
            # is written restores again, to the same snapshot.
            done = rules.restored_step(
                new_id(), self._clock(), run.agent_session_id, run.loop_id, start
            )
            await self._steps.append_agent_steps(run.ctx, run.agent_session_id, run.epoch, [done])
        return None

    async def _unprepared(
        self, run: RunState, refused: InfraException, *, restoring: bool
    ) -> LoopRun:
        """How a run stops when its workspace cannot be prepared: its end or
        its park names the refusal's cause, so a person reads why without the
        log. `restoring` when the workspace was to start from a snapshot."""
        cause = refused.cause if isinstance(refused, IsolationRefused) else None
        if isinstance(refused, IsolationRefused) and refused.clears:
            # No host can give it the workspace yet: no weaker one, and
            # no call spent. It waits on the resource and asks again.
            log.warning("session %s waits for a workspace: %s", run.agent_session_id, refused)
            retry_at = self._clock() + self._options.agent_workspace_wait
            park = Park(
                reason=ParkReason.RESOURCE,
                unlock=AGENT_WORKSPACE_UNLOCK,
                retry_at=retry_at,
                unsettled=not run.resumed,
                cause=cause,
            )
            return await self._park(run, park)
        if refused.code == ISOLATION_REFUSED:
            # A provider that cannot meet the spec refuses it whole: no
            # weaker workspace, and no call spent on a loop that cannot run.
            log.warning("session %s has no workspace: %s", run.agent_session_id, refused)
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
            unsettled=not run.resumed,
            cause=LOST_SNAPSHOT if restoring else LOST_SOURCE,
        )
        return await self._park(run, park)

    async def _drive(self, run: RunState, history: Sequence[AgentStep]) -> LoopRun:
        # What a lost run left in flight: a model request with no response is
        # closed, and its hold settled at the whole of it, since the call was
        # sent and is usually billed. Its tool calls are settled below, each
        # by its effect.
        for request in rules.unanswered_requests(history, run.loop_id):
            await self._close_lost(run, request)
        if not run.resumed:
            # A call a lost run may have started is settled by its effect
            # before anything else, a park included: once a run parks with a
            # call open, the call is one it held back and never ran.
            stopped = await self._recover_calls(run)
            if stopped is not None:
                return stopped
        while True:
            # The run read its history whole once; each turn reads only what
            # was added since, so a long session costs a turn no more.
            history = await self._history(run.ctx, run.agent_session_id, history)
            loop = rules.OpenLoop(run.loop_id, run.start_seq, None)
            if rules.asked(history, loop, ControlCommand.CANCEL) is not None:
                return await self._cancelled(run)
            if rules.pause_waits(history, loop):
                return await self._park(run, Park(reason=ParkReason.PAUSE, unlock="resume"))
            response = rules.latest_response(history, run.loop_id)
            heard: Heard | None = None
            if response is not None:
                calls = rules.open_calls(history, response)
                if calls:
                    stopped = await self._tool_turn(run, history, response, calls)
                    if stopped is not None:
                        return stopped
                    continue
                accepted = rules.accepted_outcome(history, response)
                if accepted is not None:
                    # The result gate accepted a result this turn submitted,
                    # and every call of the turn is answered.
                    return await self._end(run, accepted)
                if rules.question_waits(history, response):
                    # The agent asked its person, and every call of the turn
                    # is answered: no model call until a principal's message
                    # answers it, after a lost run as well.
                    return await self._park(run, QUESTION)
                if rules.children_wait(history, response) and not await self._past_deadline(run):
                    # The agent waits on its sub-agents, and nothing has come
                    # since the request it answered: no model call until a
                    # child's report wakes it, after a lost run as well. Past
                    # the tree's deadline no report can come, since every
                    # child parks on it too: the loop parks on it below.
                    return await self._wait_on_children(run, history, response)
                if rules.ended_turn(response):
                    # The model's turn ended: what its asks in line came to
                    # is told first. While one still waits, the loop waits in
                    # line, with no model call until a grant or an end,
                    # unless something came that the model has not read: then
                    # it reads it, and the turn is not judged. Past the
                    # tree's deadline it waits in no line: it parks on the
                    # deadline below, and leaves every line there.
                    heard = await self._hear(run, history)
                    if heard.told:
                        history = await self._history(run.ctx, run.agent_session_id, history)
                    if (
                        heard.waiting
                        and not lines.unheard(history, response)
                        and not await self._past_deadline(run)
                    ):
                        return await self._wait_in_line(run, history, heard.waiting)
                if (
                    not response.as_tool_uses()
                    and not rules.judged(history, response)
                    and not (heard is not None and heard.waiting)
                ):
                    stopped = await self._judge(run, history, response)
                    if stopped is not None:
                        return stopped
                    continue
            trip = self._tripped(run, history)
            if trip is not None and trip.limit is Limit.DEADLINE:
                # A person may have moved the deadline since this run read it,
                # and the move unlocks only a park it finds: the run reads it
                # again before it parks on it.
                run.deadline = (await self._agents.tree_of(run.ctx, run.agent_session_id)).deadline
                trip = self._tripped(run, history)
            if trip is not None:
                if trip.outcome is not None:
                    return await self._end(run, trip.outcome)
                if trip.park is not None:
                    if trip.limit is Limit.DEADLINE:
                        # Out of time, the session waits on no resource and
                        # holds none: it leaves every line, so no grant holds
                        # one for a session that cannot use it, and gives
                        # back each lease its asks hold.
                        await self._leases.leave(
                            run.ctx, WaiterKind.AGENT_SESSION, run.agent_session_id
                        )
                        await self._give_back(run, history)
                    return await self._park(run, trip.park)
                await self._keep(run)
                return self._result(run, RunEnd.YIELDED)
            repeated = rules.repeated_failure(history, run.loop_id, self._options.repeats_noticed)
            if repeated is not None:
                # Written before the next request, which reads it.
                await self._notice(run, repeated)
                history = await self._history(run.ctx, run.agent_session_id, history)
            if heard is None and (await self._hear(run, history)).told:
                # A grant, a request's end, or a lease's end that came while
                # the session ran, or since an earlier loop of it, is told
                # before the next request, which reads it.
                history = await self._history(run.ctx, run.agent_session_id, history)
            stopped = await self._model_turn(run, history)
            if stopped is not None:
                return stopped

    def _tripped(self, run: RunState, history: Sequence[AgentStep]) -> Trip | None:
        return tripped(
            run.kind.limits,
            tally_loop(history, run.loop_id),
            now=self._clock(),
            run_started_at=run.started_at,
            deadline=run.deadline,
        )

    async def _judge(
        self, run: RunState, history: Sequence[AgentStep], response: AgentStep
    ) -> LoopRun | None:
        """A turn that called no tool, under the kind's done rule: an answer
        ends the loop, and a delivery turn earns a nudge, recorded as a step
        so the next request never holds two of the model's turns in a row,
        until the nudges run out."""
        header = response.header
        if isinstance(header, ModelResponseHeader) and header.stop_reason is StopReason.PAUSE:
            await self._notice(run, rules.PAUSED)
            return None
        turn = after_turn(run.kind, response, rules.nudges_before(history, response))
        if turn is Turn.ANSWERED:
            return await self._end(run, LoopOutcome.SUCCEEDED)
        if turn is Turn.EXHAUSTED:
            return await self._end(run, LoopOutcome.INCONCLUSIVE)
        await self._notice(run, rules.nudge_text(run.kind))
        return None

    async def _past_deadline(self, run: RunState) -> bool:
        """Whether the tree's deadline has passed. A person may have moved it
        since this run read it, so a passed one is read again."""
        if run.deadline is None or self._clock() < run.deadline:
            return False
        run.deadline = (await self._agents.tree_of(run.ctx, run.agent_session_id)).deadline
        return run.deadline is not None and self._clock() >= run.deadline

    async def _policies_above(
        self, ctx: TenantContext, session: AgentSession
    ) -> tuple[ToolPolicyLayer, ...] | None:
        """The policy layers of the kinds above `session` in its tree, each
        at the version its session pinned, an ancestor marked deleted among
        them. None when a session above it is past its purge: its kind is
        gone with its row, and no layer stands in for it."""
        if session.parent_id is None:
            return ()
        above = await self._sessions.get_ancestors(ctx, session.id)
        if len(above) < session.depth - 1:
            return None
        return tuple(self._kinds.get(agent.kind, agent.version).policy for agent in above)


def lost_agent_workspace(park: Park) -> bool:
    """Whether a park waits for a person on a workspace that was lost."""
    return park.reason is ParkReason.PERSON and park.unlock == AGENT_WORKSPACE_UNLOCK
