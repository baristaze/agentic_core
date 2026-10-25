"""What a lost run left in flight, settled before a run does anything
else: a model request with no response is closed and its hold settled
whole, and each call it may have started is settled by its effect, never
run as new."""

import contextlib
from collections.abc import Sequence

from acme.om.agent_steps.types.agent_step import AgentStep
from acme.om.agent_steps.types.header import (
    ControlCommand,
    ModelRequestHeader,
    Park,
)
from acme.om.agents import loop_rules as rules
from acme.om.agents.impl.loop_core import (
    LoopCore,
    RunState,
)
from acme.om.agents.types.run import LoopRun
from acme.om.base import new_id
from acme.om.exceptions import (
    NotFound,
)
from acme.om.spend_budgets.types.usage import CallLabels, CallSite
from acme.om.tools.types.tool import ToolMode


class LoopRecovery(LoopCore):
    """What a lost run left: its open calls, each settled by its effect,
    and its unanswered model request, closed."""

    async def _recover_calls(self, run: RunState, history: Sequence[AgentStep]) -> LoopRun | None:
        """The calls a lost run left open, each settled by its effect before
        the loop does anything else: none of them is ever run as new. A call
        that would wait for a person, or whose principal lapsed, is answered
        as stopped with its outcome unknown, rather than parked open. A
        cancel that waits answers them all so. The first call that parks the
        loop as it is settled, on the job it started or on the budget that
        refused one, parks it there once every other call is settled, so
        nothing asks or starts it a second time. A job's call after it is
        left open for the run that resumes, whose start of it attaches to
        the work under its key. `history` is what the run read at its start,
        and it reads on from there."""
        history = await self._history(run.ctx, run.agent_session_id, history, own=run.own)
        response = rules.latest_response(history, run.loop_id)
        if response is None:
            return None
        lost = [(use, request) for use, request in rules.open_calls(history, response) if request]
        if not lost:
            return None
        loop = rules.OpenLoop(run.loop_id, run.start_seq, None)
        if rules.asked(history, loop, ControlCommand.CANCEL) is not None:
            return await self._cancelled(run)
        park: Park | None = None
        for use, request in lost:
            tool = self._offered(run, request)
            if park is not None and tool is not None and tool.spec.mode is ToolMode.JOB:
                continue
            settled = await self._settle_call(run, history, use, request, fresh=False)
            if settled.cancelled:
                return await self._cancelled(run, park)
            park = park or settled.park
        if park is not None:
            return await self._park(run, park)
        return None

    async def _close_lost(self, run: RunState, request: AgentStep) -> None:
        """A request a lost run left unanswered: its hold settled whole, its
        usage record named from the request and the session, since this
        run's gate never held it, and the request closed as abandoned."""
        header = request.header
        closing = rules.abandoned_step(new_id(), self._clock(), request)
        if isinstance(header, ModelRequestHeader) and header.spend_hold_id is not None:
            provider, _, model = header.fill.partition("/")
            session = run.session
            labels = CallLabels(
                agent_session_id=run.agent_session_id,
                tree_id=session.root_id,
                agent_kind=session.kind,
                kind_version=session.kind_version,
                role=header.role,
                provider=provider,
                model=model,
            )
            site = CallSite(
                loop_id=run.loop_id, agent_step_id=closing.id, latency_ms=0, labels=labels
            )
            with contextlib.suppress(NotFound):
                await self._gate.settle(run.ctx, header.spend_hold_id, None, billed=True, site=site)
        await self._append(run, [closing])
