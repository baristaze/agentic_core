"""How a run stops, and what it waits on: a principal's cancel, the
loop's end and its report to a parent, a park, a wait on its children,
and a wait in line for a resource, with what each ask in line came to
told to the model."""

import logging
from collections.abc import Sequence

from acme.om.agent_sessions.kinds import AGENT_SESSION_WAITER
from acme.om.agent_steps.types.agent_step import AgentStep
from acme.om.agent_steps.types.header import (
    ControlCommand,
    LoopOutcome,
    Park,
    ToolFailure,
)
from acme.om.agents import line_rules as lines
from acme.om.agents import loop_rules as rules
from acme.om.agents.impl.loop_core import (
    Heard,
    LoopCore,
    RunState,
)
from acme.om.agents.rules import CHILDREN_PARK, ends_parents_wait, notes_parent
from acme.om.agents.types.line import InLine
from acme.om.agents.types.run import LoopRun, RunEnd
from acme.om.attribution.types.authority import CallReach
from acme.om.base import new_id
from acme.om.exceptions import (
    LeaseEnded,
    NotAuthorized,
    NotFound,
    PrincipalLapsed,
)
from acme.om.leases.types.lease import LeaseStatus
from acme.om.leases.types.request import RequestStatus, Standing
from acme.om.tools.rules import (
    response_id,
)

log = logging.getLogger(__name__)


class LoopParks(LoopCore):
    """How a run stops: a cancel, an end, a park, and the waits it parks
    in, on its children and in line."""

    async def _cancelled(self, run: RunState, park: Park | None = None) -> LoopRun:
        """A principal's cancel: every call still open is answered as stopped,
        so the history holds no open call, and the loop ends `cancelled`,
        and so do its children's. Every job the loop started is cancelled
        with it, one whose `park` this run has not yet written as well."""
        history = await self._history(run.ctx, run.agent_session_id)
        jobs = rules.started_jobs(history, run.loop_id)
        if park is not None and park.job is not None and park.job.key not in jobs:
            request = next(step for step in history if step.id == park.job.key)
            jobs[park.job.key] = rules.StartedJob(request, park, None)
        why = f"stopped by a principal's {ControlCommand.CANCEL.value}; the job was cancelled"
        await self._stop_jobs(run, list(jobs.values()), why)
        response = rules.latest_response(history, run.loop_id)
        if response is not None:
            calls = [
                (use, request)
                for use, request in rules.open_calls(history, response)
                if request is None or request.id not in jobs
            ]
            if calls:
                requests = await self._requests(run, response, calls)
                answers = [
                    self._cancelled_call(run, request).model_copy(
                        update={"id": response_id(request, run.epoch)}
                    )
                    for _, request in requests
                ]
                await self._append(run, answers)
        return await self._end(run, LoopOutcome.CANCELLED)

    def _cancelled_call(self, run: RunState, request: AgentStep) -> AgentStep:
        """A cancel's answer to a call still open: stopped before it ran, or,
        for one a lost run may have started, stopped with its outcome
        unknown, so the model verifies before it calls again."""
        if self._never_ran(run, request):
            text = f"stopped by a principal's {ControlCommand.CANCEL.value} before it ran"
            return self._stopped(request, text, ToolFailure.INTERRUPTED)
        return self._lost(request)

    async def _end(
        self, run: RunState, outcome: LoopOutcome, *, cause: str | None = None
    ) -> LoopRun:
        if run.jobs:
            why = f"the loop ended {outcome.value}; the job was cancelled"
            await self._stop_jobs(run, list(run.jobs.values()), why)
        await self._keep(run)
        if run.session.parent_id is not None:
            # Before the loop is closed: a run lost in between leaves it open,
            # and the run that ends it again repeats the report, written once.
            await self._report(run, outcome=outcome)
        # A loop that ends waits on nothing: its session leaves every line
        # before the loop is closed, and a run that ends it again leaves
        # again, finding nothing.
        await self._leases.leave(run.ctx, AGENT_SESSION_WAITER, run.agent_session_id)
        step = rules.ended_step(
            new_id(), self._clock(), run.agent_session_id, run.loop_id, outcome, cause=cause
        )
        await self._append(run, [step])
        await self._sessions.project_status(run.ctx, run.agent_session_id)
        if outcome is LoopOutcome.CANCELLED:
            await self._agents.cancel_children(run.ctx, run.agent_session_id)
        return self._result(run, RunEnd.ENDED, outcome=outcome)

    async def _park(self, run: RunState, park: Park) -> LoopRun:
        await self._keep(run)
        await self._sessions.park(run.ctx, run.agent_session_id, run.epoch, run.loop_id, park)
        if park.wait_line is not None and park.job is not None:
            # A job's call in line: a grant, or the request's end, that came
            # after the run read the request and before the park found no
            # park to clear, so the run reads it again and clears its own.
            standing = await self._standing(run, park.wait_line.request_id)
            if standing is None or standing.request.status is not RequestStatus.WAITING:
                await self._sessions.wake_agent_session(run.ctx, run.agent_session_id, park)
        parent_id = run.session.parent_id
        if parent_id is not None and notes_parent(park):
            await self._report(run, park=park)
        elif parent_id is not None and ends_parents_wait(park):
            # A parent that waits on its children leaves that wait, and parks
            # on the deadline itself.
            await self._sessions.wake_agent_session(run.ctx, parent_id, CHILDREN_PARK)
        return self._result(run, RunEnd.PARKED, park=park)

    async def _wait_on_children(
        self, run: RunState, history: Sequence[AgentStep], response: AgentStep
    ) -> LoopRun:
        """Parks on the children. A report that landed after the history was
        read and before the park found no park to clear, so the run reads
        what came since and clears the park itself; one that lands after
        the park clears it as it lands. The tree's deadline, passed in
        between, is read the same way: a child that parked on it found no
        park to clear."""
        parked = await self._park(run, CHILDREN_PARK)
        history = await self._history(run.ctx, run.agent_session_id, history, own=run.own)
        passed = run.deadline is not None and self._clock() >= run.deadline
        if passed or not rules.children_wait(history, response):
            await self._sessions.wake_agent_session(run.ctx, run.agent_session_id, CHILDREN_PARK)
        return parked

    async def _hear(self, run: RunState, history: Sequence[AgentStep]) -> Heard:
        """Reads where each ask of the session stands, and tells the model, as
        the engine's notice, each answer it has not been told: a grant while
        its lease lives, an end without a lease, the lease's end. A session
        that asked nothing in line reads nothing."""
        standings = await self._standings(run, history)
        told = {step.id for step in history}
        owed = [
            notice for ask, standing in standings for notice in lines.untold(standing, ask, told)
        ]
        if owed:
            principal = await self._attribution.call_principal(run.ctx, run.agent_session_id)
            now = self._clock()
            notices = [
                rules.notice_step(
                    agent_step_id, now, run.agent_session_id, run.loop_id, principal, text
                )
                for agent_step_id, text in owed
            ]
            await self._append(run, notices)
        waiting = [
            standing
            for _, standing in standings
            if standing.request.status is RequestStatus.WAITING
        ]
        return Heard(waiting=waiting, told=bool(owed))

    async def _standings(
        self, run: RunState, history: Sequence[AgentStep]
    ) -> list[tuple[lines.Ask, Standing]]:
        """Where each ask of the session stands, across its loops, but one
        whose final answer the model was told already: nothing more comes of
        it, so the reads stop growing once each end is told."""
        tools = [
            tool.spec.name
            for tool in run.registry.tools()
            if issubclass(tool.spec.output_model, InLine)
        ]
        if not tools:
            return []
        told = {step.id for step in history}
        standings: list[tuple[lines.Ask, Standing]] = []
        for ask in lines.asks(history, tools):
            if lines.done(ask, told):
                continue
            try:
                standing = await self._leases.get_request(run.ctx, ask.request_id)
            except NotFound:
                continue  # past its retention: nothing more comes of it
            standings.append((ask, standing))
        return standings

    async def _wait_in_line(
        self, run: RunState, history: Sequence[AgentStep], waiting: Sequence[Standing]
    ) -> LoopRun:
        """Parks in line. An answer that landed after the asks were read and
        before the park found no park to clear, so the run reads the asks
        again once it has parked and clears the park itself; one that lands
        after the park clears it as it lands."""
        park = lines.line_park(waiting)
        parked = await self._park(run, park)
        history = await self._history(run.ctx, run.agent_session_id, history, own=run.own)
        told = {step.id for step in history}
        for ask, standing in await self._standings(run, history):
            if lines.untold(standing, ask, told):
                await self._sessions.wake_agent_session(run.ctx, run.agent_session_id, park)
                break
        return parked

    async def _give_back(self, run: RunState, history: Sequence[AgentStep]) -> None:
        """Releases each live lease the session's asks hold, as its holder: the
        principal the session's calls run under, asked of attribution as a
        call's is. Each lease's end, and each request's end without a lease,
        is told before the park, once. A principal that no longer holds the
        session's calls, or is not a lease's holder, releases nothing: that
        lease runs to its term."""
        held = [
            standing.lease
            for _, standing in await self._standings(run, history)
            if standing.lease is not None and standing.lease.status is LeaseStatus.ACTIVE
        ]
        if held:
            reach = CallReach(outward=False, holds_private=False)
            try:
                authority = await self._attribution.authorize_call(
                    run.ctx, run.agent_session_id, reach
                )
            except (NotAuthorized, PrincipalLapsed) as refused:
                log.warning(
                    "session %s keeps its leases to their term: %s", run.agent_session_id, refused
                )
            else:
                for lease in held:
                    try:
                        await self._leases.release(authority.context, lease.id)
                    except (NotAuthorized, LeaseEnded) as refused:
                        log.warning("lease %s runs to its term: %s", lease.id, refused)
        await self._hear(run, history)
