"""The tool calls a response asked for, and the jobs they start: each
call's request persisted before it is decided, each one gated and run,
or settled by its effect after a lost run, and answered before the next
model call. A job parks the loop until its completion, its grant, or
its deadline."""

import asyncio
import contextlib
import itertools
import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from acme.infra.transports import OutputSink
from acme.om.agent_sessions.kinds import AGENT_SESSION_WAITER
from acme.om.agent_steps.types.agent_step import Actor, AgentStep, AgentStepType, Origin
from acme.om.agent_steps.types.content import ToolUseBlock
from acme.om.agent_steps.types.header import (
    AcceptedResult,
    ControlCommand,
    JobPark,
    Park,
    ParkReason,
    SwitchedHeader,
    ToolFailure,
    ToolRequestHeader,
    ToolResponseHeader,
)
from acme.om.agent_steps.types.stream import ToolOutputPart
from acme.om.agents import line_rules as lines
from acme.om.agents import loop_rules as rules
from acme.om.agents.impl.loop_core import (
    LoopCore,
    RunState,
    Settled,
)
from acme.om.agents.types.result import Result
from acme.om.agents.types.run import LoopRun
from acme.om.attribution.rules import PRINCIPAL_PARK
from acme.om.attribution.types.principal import AgentRef
from acme.om.base import new_id, thaw_mapping
from acme.om.context import TenantContext
from acme.om.exceptions import (
    GateParked,
    NotAuthorized,
    PrincipalLapsed,
    SpendBudgetRefused,
    SpenderUnknown,
    StaleWriter,
    ToolFailed,
)
from acme.om.leases.types.request import RequestStatus
from acme.om.spend_budgets.rules import spend_budget_park
from acme.om.tools.native.load_toolset import load, loads
from acme.om.tools.rules import (
    job_deadline,
    response,
    response_id,
    tool_request,
)
from acme.om.tools.tool import ToolInterface
from acme.om.tools.types.call import (
    MAX_REPORT,
    GateOutcome,
    JobHandle,
    JobNotStarted,
)
from acme.om.tools.types.tool import ToolMode
from acme.om.tools.types.toolset import LOAD_TOOLSET

log = logging.getLogger(__name__)


NOT_OFFERED = "unknown"
"""The class a call's request records when the registry held no tool of its
name as the model made it. A tool a load in the same turn adds is never
that call's, whatever the registry holds by the time it settles."""


class LoopToolTurn(LoopCore):
    """The calls a response asked for: each request persisted first, then
    gated, run, recovered, or started as a job, and answered."""

    async def _tool_turn(
        self,
        run: RunState,
        history: Sequence[AgentStep],
        response: AgentStep,
        calls: Sequence[tuple[ToolUseBlock, AgentStep | None]],
    ) -> LoopRun | None:
        """Every open call of a response: its request persisted first, then
        each one gated, run or recovered, and answered, in the order the
        model made them. A call that waits for a person parks the loop once
        the others are answered."""
        requests = await self._requests(run, response, calls)
        asked = False
        for use, request in requests:
            settled = await self._settle_call(
                run, history, use, request, fresh=self._never_ran(run, request)
            )
            if settled.cancelled:
                return await self._cancelled(run)
            if settled.park is not None:
                return await self._park(run, settled.park)
            asked = asked or settled.asked
        if asked:
            return await self._park(
                run, Park(reason=ParkReason.PERSON, unlock=rules.APPROVAL_UNLOCK)
            )
        return None

    async def _requests(
        self,
        run: RunState,
        response: AgentStep,
        calls: Sequence[tuple[ToolUseBlock, AgentStep | None]],
    ) -> list[tuple[ToolUseBlock, AgentStep]]:
        """The request of every call, writing those not yet written, in one
        append, before any is decided; the run keeps the ids it wrote."""
        missing = [use for use, request in calls if request is None]
        if not missing:
            return [(use, request) for use, request in calls if request is not None]
        principal = await self._attribution.call_principal(run.ctx, run.agent_session_id)
        agent = AgentRef(
            kind=run.kind.name, version=run.kind.version, agent_session_id=run.agent_session_id
        )
        hashes = [
            await self._tools.input_hash(run.ctx, run.agent_session_id, use.input)
            for use in missing
        ]
        now = self._clock()
        built = [
            tool_request(
                new_id(),
                now,
                response,
                use,
                self._class_of(run, use.name),
                input_hash=hashed,
                principal=principal,
                authority=run.kind.authority,
                agent=agent,
            )
            for use, hashed in zip(missing, hashes, strict=True)
        ]
        stored = await self._steps.append_agent_steps(
            run.ctx, run.agent_session_id, run.epoch, built
        )
        by_use = {_use_id(step): step for step in stored}
        run.made.update(step.id for step in stored)
        return [(use, request or by_use[use.id]) for use, request in calls]

    async def _settle_call(
        self,
        run: RunState,
        history: Sequence[AgentStep],
        use: ToolUseBlock,
        request: AgentStep,
        *,
        fresh: bool,
    ) -> Settled:
        ctx = run.ctx
        assert run.workspace is not None
        tool = self._offered(run, request)
        if tool is None and run.registry.get(use.name) is not None:
            # A load in the call's own turn added its tool after the call was
            # made, and a run that settles it after a lost one holds it now:
            # it is refused as a tool its request did not offer.
            text = (
                f"there was no tool named {use.name!r} when this call was made; "
                "a toolset's tools are callable from the request after its load"
            )
            await self._answer(
                run, request, self._stopped(request, text, ToolFailure.INVALID_INPUT)
            )
            return Settled()
        if tool is not None and tool.spec.mode is ToolMode.JOB:
            started = rules.started_jobs(history, run.loop_id).get(request.id)
            if started is not None:
                # A job a run started is settled by the job alone: it is never
                # asked about, gated, or started again.
                return await self._settle_job(run, started)
        try:
            gate = await self._tools.gate(
                ctx,
                run.registry,
                run.kind.policy,
                request,
                use.input,
                run.workspace,
                holds_private=rules.holds_private(run.session, run.kind, run.registry, history),
                # A fresh call's preflight keeps the tree's deadline. A call a
                # lost run may have started is settled by its effect, which
                # the transport's record answers whatever the time.
                tree_deadline=run.deadline if fresh else None,
                above=run.above,
            )
        except PrincipalLapsed:
            if not fresh:
                await self._answer(run, request, self._lost(request))
                return Settled()
            return Settled(park=PRINCIPAL_PARK)
        if gate.outcome is GateOutcome.REFUSE and gate.response is not None:
            await self._answer(run, request, gate.response)
            return Settled()
        if gate.outcome is GateOutcome.ASK or gate.authority is None:
            if not fresh:
                # It may have run once; it never waits open for a second.
                await self._answer(run, request, self._lost(request))
                return Settled()
            return Settled(asked=True)
        if use.name == run.kind.result_tool:
            return await self._submit(run, request, use)
        if use.name == LOAD_TOOLSET:
            return await self._load(run, history, request, use)
        assert tool is not None  # the gate resolved it
        if tool.spec.mode is ToolMode.JOB:
            return await self._start_job(
                run, gate.authority.context, tool, request, use.input, history
            )
        # The call runs under the live context of the principal attribution
        # answered for it, asked again for this call.
        call_ctx = gate.authority.context
        # The call's output streams as the response it adds up to: the stream
        # opens before the call runs, and completes once that step is stored.
        agent_step_id = response_id(request, run.epoch)
        on_output = self._output(run, request)
        self._tell(run, self._sink.opened, agent_step_id)
        try:
            try:
                if fresh:
                    ran = await self._execute(run, call_ctx, tool, request, use.input, on_output)
                else:
                    ran = await self._tools.recover(
                        call_ctx,
                        run.registry,
                        request,
                        use.input,
                        run.workspace,
                        epoch=run.epoch,
                        tree_deadline=run.deadline,
                        on_output=on_output,
                    )
            except NotAuthorized as refused:
                ran = self._stopped(request, refused.message, ToolFailure.DENIED)
            if isinstance(ran, ControlCommand):
                text = f"stopped by a principal's {ran.value} before it answered"
                stopped = self._stopped(request, text, ToolFailure.INTERRUPTED)
                await self._answer(run, request, stopped)
                return Settled(cancelled=ran is ControlCommand.CANCEL)
            await self._answer(run, request, ran)
            return Settled()
        finally:
            # Over, whichever way: its step is stored, or the call failed.
            self._tell(run, self._sink.completed, agent_step_id)

    async def _execute(
        self,
        run: RunState,
        call_ctx: TenantContext,
        tool: ToolInterface,
        request: AgentStep,
        call_input: Mapping[str, Any],
        on_output: OutputSink,
    ) -> AgentStep | ControlCommand:
        """Runs a call while its controls are read: a cancel, or an interrupt
        of a tool that may be stopped, ends it, and answers the control that
        did."""
        assert run.workspace is not None
        task = asyncio.ensure_future(
            self._tools.execute(
                call_ctx,
                run.registry,
                request,
                call_input,
                run.workspace,
                epoch=run.epoch,
                tree_deadline=run.deadline,
                on_output=on_output,
            )
        )
        seen = request.seq
        try:
            while True:
                waiter = asyncio.ensure_future(
                    self._sleep(self._options.control_poll.total_seconds())
                )
                done, _ = await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
                waiter.cancel()
                if task in done:
                    return task.result()
                later = await self._since(run, seen)
                if later:
                    seen = later[-1].seq
                command = rules.stops_call(later, request, tool.spec.interruptible)
                if command is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
                    return command
        finally:
            if not task.done():
                task.cancel()

    async def _start_job(
        self,
        run: RunState,
        call_ctx: TenantContext,
        tool: ToolInterface,
        request: AgentStep,
        call_input: Mapping[str, Any],
        history: Sequence[AgentStep],
    ) -> Settled:
        """Starts a job its gate let run, by a deadline of its own never later
        than the tree's, and parks the loop on it. A job that spends passes
        the budget gate first, paid for by whoever its call was asked for: a
        refusal parks on the budget, a gate that parks the job parks where it
        says, and nobody to pay parks for a person, as a model call's gate
        does; nothing starts. A job that will not start is answered with its
        failure, and its hold released only when the start was refused before
        any work began."""
        assert run.workspace is not None
        now = self._clock()
        deadline = job_deadline(now, tool.spec.timeout, run.deadline)
        if deadline <= now:
            text = "the task's deadline has passed, so the job was not started"
            await self._answer(run, request, self._stopped(request, text, ToolFailure.TIMEOUT))
            return Settled()
        hold: UUID | None = None
        rate = tool.spec.rate_micros_per_hour
        if rate is not None:
            spender = rules.asked_by(history, request)
            if spender is None:
                return Settled(park=Park(reason=ParkReason.PERSON, unlock=rules.SPENDER_UNLOCK))
            try:
                hold = await self._gate.authorize_job(
                    run.ctx, run.agent_session_id, spender, tool.spec.name, rate, deadline
                )
            except SpendBudgetRefused as refused:
                return Settled(park=spend_budget_park(refused.refusal, self._clock()))
            except GateParked as parked:
                return Settled(park=parked.park)
            except SpenderUnknown as unknown:
                log.info("session %s: %s", run.agent_session_id, unknown.message)
                return Settled(park=Park(reason=ParkReason.PERSON, unlock=rules.SPENDER_UNLOCK))
        try:
            started = await self._tools.start_job(
                call_ctx,
                run.registry,
                request,
                call_input,
                run.workspace,
                epoch=run.epoch,
                tree_deadline=deadline,
            )
        except BaseException:
            if hold is not None:
                with contextlib.suppress(Exception):
                    await self._gate.settle_job(run.ctx, hold, None, started=True)
            raise
        if isinstance(started, JobNotStarted):
            if hold is not None:
                # Refused before any work began: nothing ran.
                await self._gate.settle_job(run.ctx, hold, None, started=False)
            await self._answer(run, request, started.response)
            return Settled()
        if isinstance(started, AgentStep):
            if hold is not None:
                # Its tool ran and failed, whatever the class: the work may
                # have started, so its hold counts whole.
                await self._gate.settle_job(run.ctx, hold, None, started=True)
            await self._answer(run, request, started)
            return Settled()
        job = JobPark(
            key=started.key,
            handle=started.handle,
            spend_hold_id=hold,
            request_id=started.request_id,
        )
        park = Park(
            reason=ParkReason.JOB, unlock=str(started.key), retry_at=started.deadline, job=job
        )
        if started.request_id is not None:
            # Its tool asked in line: the grant starts the work.
            waiting = rules.StartedJob(request, park, None)
            return await self._wait_for_start(run, waiting, started.request_id)
        return Settled(park=park)

    async def _settle_job(self, run: RunState, started: rules.StartedJob) -> Settled:
        """A started job's call, when the loop takes it up again: answered
        from its completion once one arrived; cancelled and answered as out of
        time once its deadline passed; otherwise the loop parks on it again,
        since a woken loop trusts nothing that woke it."""
        if started.completion is not None:
            await self._close_job(run, started, None)
            return Settled()
        if started.park.wait_line is not None:
            return await self._wait_for_start(run, started, started.park.wait_line.request_id)
        retry_at = started.park.retry_at
        assert retry_at is not None  # a job park tries again at its deadline
        if self._clock() >= retry_at:
            await self._close_job(run, started, (rules.JOB_DEADLINE, ToolFailure.TIMEOUT))
            return Settled()
        return Settled(park=started.park)

    async def _wait_for_start(
        self, run: RunState, started: rules.StartedJob, request_id: UUID
    ) -> Settled:
        """A job whose tool asked in line: the grant starts it, in its own
        commit. Granted, the loop's park moves to the job's. Still waiting,
        the loop parks in line, naming the job, its place, and its estimate,
        with no model call until the grant or the request's end; past the
        job's deadline, it is cancelled. Out of line without a lease, no job
        started: the call is answered so, and its hold released."""
        job, deadline = started.job, started.park.retry_at
        assert deadline is not None  # a job's park tries again at its deadline
        standing = await self._standing(run, request_id)
        if standing is not None and standing.lease is not None:
            on_job = Park(reason=ParkReason.JOB, unlock=str(job.key), retry_at=deadline, job=job)
            return await self._settle_job(run, replace(started, park=on_job))
        if standing is not None and standing.request.status is RequestStatus.WAITING:
            if self._clock() >= deadline:
                stopped = (rules.JOB_UNSTARTED, ToolFailure.TIMEOUT)
                await self._close_job(run, started, stopped, ran=False)
                return Settled()
            return Settled(park=lines.job_line_park(standing, job, deadline))
        if job.spend_hold_id is not None:
            await self._gate.settle_job(run.ctx, job.spend_hold_id, None, started=False)
        if standing is None:
            text, failure = rules.JOB_GONE, ToolFailure.PERMANENT
        else:
            text, failure = lines.unstarted(standing.request)
        await self._answer(run, started.request, self._stopped(started.request, text, failure))
        run.jobs.pop(job.key, None)
        return Settled()

    async def _close_job(
        self,
        run: RunState,
        started: rules.StartedJob,
        stopped: tuple[str, ToolFailure] | None,
        *,
        ran: bool = True,
    ) -> None:
        """Answers a started job's call. With no reason to stop, from what its
        completion says, its hold settled at the cost it reported; with one,
        the work is cancelled first, its hold counted whole, and the call
        answered with the reason and its class. A job its grant never started
        (`ran` False) ran nothing, so its hold is released. The hold settles
        before the answer is written, so a run lost between them settles it
        again, and the first settlement counts."""
        job, request, completion = started.job, started.request, started.completion
        if stopped is None:
            assert completion is not None
            text = completion.text or "the job completed and reported nothing"
            answer = response(
                new_id(), self._clock(), request, text, completion.failure, limit=MAX_REPORT
            )
            cost = completion.cost_micros
        else:
            await self._cancel_job(run, started)
            answer = self._stopped(request, *stopped)
            cost = None
        if job.spend_hold_id is not None:
            await self._gate.settle_job(run.ctx, job.spend_hold_id, cost, started=ran)
        await self._answer(run, request, answer)
        run.jobs.pop(job.key, None)

    async def _cancel_job(self, run: RunState, started: rules.StartedJob) -> None:
        """Ends the work. A job whose tool asked in line names its request,
        in line or granted, so the tool's cancel ends its ask, or its lease.
        A tool that fails to end it is logged and the loop goes on: the work
        was started to end by its deadline, which bounds it whatever happens
        here."""
        header = started.request.header
        assert isinstance(header, ToolRequestHeader)
        handle = JobHandle(
            tool=header.tool,
            key=started.job.key,
            handle=started.job.handle,
            deadline=started.park.retry_at or self._clock(),
            request_id=started.job.request_id,
        )
        try:
            await self._tools.cancel_job(run.ctx, run.registry, handle)
        except StaleWriter:
            raise
        except Exception:
            log.exception("session %s: job %s was not cancelled", run.agent_session_id, handle.key)

    async def _stop_jobs(self, run: RunState, jobs: Sequence[rules.StartedJob], why: str) -> None:
        """Every job the loop still waits on, as the loop stops: one that
        completed is answered from its completion; any other is cancelled
        and answered with `why`, so no job outlives its loop. With a job in
        line, the session leaves every line first, so no grant starts a job
        for a loop that stops, and a job still in line then never started."""
        if any(started.park.wait_line is not None for started in jobs):
            await self._leases.leave(run.ctx, AGENT_SESSION_WAITER, run.agent_session_id)
        for started in jobs:
            stopped = None if started.completion is not None else (why, ToolFailure.INTERRUPTED)
            ran = True
            if stopped is not None and started.park.wait_line is not None:
                standing = await self._standing(run, started.park.wait_line.request_id)
                ran = standing is not None and standing.lease is not None
            await self._close_job(run, started, stopped, ran=ran)

    async def _submit(self, run: RunState, request: AgentStep, use: ToolUseBlock) -> Settled:
        """A result submitted through the kind's result tool: refused without
        evidence, else judged by the result gate. An accepted one ends the
        loop with the outcome it claims once every call is answered; a
        refused one goes back to the model with the reason. The verdict is
        kept in the answer's header, and the loop ends on it from there."""
        try:
            result = Result.model_validate(thaw_mapping(use.input))
        except ValidationError as refused:
            detail = "; ".join(
                f"{'.'.join(str(part) for part in item['loc']) or 'input'}: {item['msg']}"
                for item in refused.errors()
            )
            await self._answer(
                run, request, self._stopped(request, detail, ToolFailure.INVALID_INPUT)
            )
            return Settled()
        verdict = await self._agents.judge_result(run.ctx, run.agent_session_id, result)
        if not verdict.accepted or verdict.outcome is None:
            reason = verdict.reason or "the result gate refused the result"
            await self._answer(run, request, self._stopped(request, reason, ToolFailure.DENIED))
            return Settled()
        checked = "verified" if verdict.verified else "unverified"
        text = f"The result is accepted, {checked}: the loop ends {verdict.outcome.value}."
        answer = response(new_id(), self._clock(), request, text, limit=self._options.stopped_chars)
        header = answer.header
        assert isinstance(header, ToolResponseHeader)
        verdicted = header.model_copy(
            update={"accepted": AcceptedResult(outcome=verdict.outcome, verified=verdict.verified)}
        )
        # The verdict is a step: the loop ends on it once every call of the
        # turn is answered, after a park or a lost run as well.
        await self._answer(run, request, answer.model_copy(update={"header": verdicted}))
        return Settled()

    async def _load(
        self, run: RunState, history: Sequence[AgentStep], request: AgentStep, use: ToolUseBlock
    ) -> Settled:
        """A load of a toolset the kind defers: its answer, and the `switched`
        step that names the tools it adds, in one append, so no tool joins
        a request without its record. The run's registry takes them as it
        renders its next request, never for a call of this turn. A toolset
        the kind does not defer is the call's invalid input; one whose
        tools the registry holds already answers so, and switches
        nothing."""
        history = await self._history(run.ctx, run.agent_session_id, history)
        try:
            loaded = load(
                run.kind.deferred,
                (*run.session.tools, *run.attached.names),
                run.kind.waiting,
                loads(history),
                thaw_mapping(use.input),
            )
        except ToolFailed as refused:
            await self._answer(
                run, request, self._stopped(request, refused.message, refused.failure)
            )
            return Settled()
        answer = response(
            new_id(), self._clock(), request, loaded.text, limit=self._options.stopped_chars
        )
        if loaded.switch is None:
            await self._answer(run, request, answer)
            return Settled()
        switched = AgentStep(
            id=new_id(),
            created_at=self._clock(),
            agent_session_id=run.agent_session_id,
            loop_id=run.loop_id,
            type=AgentStepType.SWITCHED,
            actor=Actor.ENGINE,
            origin=Origin.ENGINE,
            refs=(request.id,),
            header=SwitchedHeader(toolset=loaded.switch),
        )
        await self._answer(run, request, answer, switched)
        return Settled()

    def _output(self, run: RunState, request: AgentStep) -> OutputSink:
        """Where a running call's output streams: numbered parts of the
        response the call adds up to, emitted and never waited on."""
        agent_step_id = response_id(request, run.epoch)
        numbers = itertools.count()

        async def on_output(channel: str, text: str) -> None:
            self._emit(
                run,
                ToolOutputPart(
                    agent_session_id=run.agent_session_id,
                    agent_step_id=agent_step_id,
                    n=next(numbers),
                    channel=channel,
                    text=text,
                ),
            )

        return on_output

    def _class_of(self, run: RunState, name: str) -> str:
        """A call's class, as its tool declares it. A name the registry does
        not hold records `NOT_OFFERED`, and is refused before any class is
        read."""
        tool = run.registry.get(name)
        return tool.spec.authorization_class if tool is not None else NOT_OFFERED

    def _offered(self, run: RunState, request: AgentStep) -> ToolInterface | None:
        """The tool a call names, when its request was offered one: a call
        recorded `NOT_OFFERED` has none, whatever the registry holds now."""
        header = request.header
        assert isinstance(header, ToolRequestHeader)
        if header.authorization_class == NOT_OFFERED:
            return None
        return run.registry.get(header.tool)


def _use_id(request: AgentStep) -> str:
    header = request.header
    return getattr(header, "tool_use_id", "")
