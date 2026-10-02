import asyncio
import contextlib
import itertools
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import Field, ValidationError

from acme.infra.exceptions import InfraException
from acme.infra.outages import Outage, OutageSignalInterface
from acme.infra.transports import OutputSink
from acme.infra.workspaces import Workspace
from acme.integrations.model_providers import ModelProvidersInterface
from acme.integrations.model_providers.calls import Finished, ModelCall, ModelReply
from acme.integrations.model_providers.failures import ModelCallFailed
from acme.integrations.model_providers.types import ErrorAnswer, ErrorKind, StopReason
from acme.om.agent_sessions import AgentSessionsManagerInterface
from acme.om.agent_sessions.limits import tally_loop, tripped
from acme.om.agent_sessions.rules import unlock_step
from acme.om.agent_sessions.types.agent_session import AgentSession, SessionStatus
from acme.om.agents import AgentsManagerInterface
from acme.om.agents import loop_rules as rules
from acme.om.agents.loop import LoopManagerInterface
from acme.om.agents.rules import after_turn
from acme.om.agents.sink import StreamSinkInterface
from acme.om.agents.types.kind import AgentKind, AgentKindCatalog
from acme.om.agents.types.result import Result, Turn
from acme.om.agents.types.run import LoopRun, RunEnd
from acme.om.attribution import AttributionManagerInterface
from acme.om.attribution.types.principal import AgentRef, Principal, PrincipalKind
from acme.om.base import Platform, new_id, thaw_mapping, utcnow
from acme.om.budgets.rules import budget_park
from acme.om.context import Permission, TenantContext
from acme.om.exceptions import (
    BudgetRefused,
    CompactionFailed,
    ContextOverflow,
    NoSpender,
    NotAuthorized,
    NotFound,
    PlatformException,
    PrincipalLapsed,
    StaleWriter,
    Unavailable,
    UnpricedModel,
    UnresolvedRole,
    ValidationFailed,
)
from acme.om.models.manager import ModelsManagerInterface
from acme.om.models.types.fill import MAIN, SUMMARIZER, Eligibility, Fill
from acme.om.steps import StepsManagerInterface
from acme.om.steps.rules import origin_of
from acme.om.steps.types.content import Content, TextBlock, ToolUseBlock
from acme.om.steps.types.header import (
    AcceptedResult,
    ControlCommand,
    InputHeader,
    LoopOutcome,
    ModelRequestHeader,
    ModelResponseHeader,
    Park,
    ParkReason,
    ToolFailure,
    ToolResponseHeader,
)
from acme.om.steps.types.step import Actor, Step, StepType
from acme.om.steps.types.stream import ToolOutputPart
from acme.om.tools import ToolsManagerInterface
from acme.om.tools.registry import ToolRegistry
from acme.om.tools.rules import ISOLATION_REFUSED, response, response_id, tool_request
from acme.om.tools.tool import ToolInterface
from acme.om.tools.types.call import GateOutcome
from acme.om.tools.types.tool import ToolMode
from acme.om.windows import WindowsManagerInterface
from acme.om.windows.gate import CallGateInterface
from acme.om.windows.rules import request_step
from acme.om.windows.types.window import RenderedRequest

log = logging.getLogger(__name__)

Sleep = Callable[[float], Awaitable[None]]
"""How the loop waits, injected beside its clock, so a test never sleeps."""

HANDOVER_UNLOCK = "give_back"
NO_JOBS = (
    "this engine starts no job: a job tool's work and its completion are not wired to its loop yet"
)


class LoopOptions(Platform):
    # The credential the loop's calls run on, as the outage signal names it:
    # the platform's own key.
    credential: str = Field(default="platform", min_length=1)
    retries: int = Field(default=2, ge=0)  # in-process retries of an error worth retrying
    retry_base: timedelta = timedelta(seconds=1)  # the first backoff, doubled after each
    # A wait longer than this is not spent in process: the loop falls back,
    # or parks on the provider.
    retry_cap: timedelta = timedelta(seconds=30)
    # The least time an outage is reported for when the loop parks on it.
    outage_wait: timedelta = timedelta(seconds=30)
    # How often a running tool's controls are read: a cancel or an interrupt
    # stops it within this.
    control_poll: timedelta = timedelta(milliseconds=500)
    page: int = Field(default=200, gt=0)  # steps one read of the history asks for
    stopped_chars: int = Field(default=2_000, gt=0)  # the most a stopped call's answer holds


@dataclass
class _Run:
    """One run of one loop: what it read when it began, and what it learns
    as it goes."""

    ctx: TenantContext
    session: AgentSession
    kind: AgentKind
    registry: ToolRegistry
    epoch: int
    loop_id: UUID
    start_seq: int
    started_at: datetime
    resumed: bool  # it resumed a park the loop wrote: the calls it held back never ran
    deadline: datetime | None = None
    workspace: Workspace | None = None
    made: set[UUID] = field(default_factory=lambda: set[UUID]())  # tool requests it wrote
    failures: int = 0  # provider errors in a row
    tried: list[Fill] = field(default_factory=lambda: [])
    refused: RenderedRequest | None = None  # a request the provider refused as too long

    @property
    def session_id(self) -> UUID:
        return self.session.id


@dataclass
class _Settled:
    """How one tool call stood once the loop dealt with it."""

    asked: bool = False  # it waits for a person's decision
    cancelled: bool = False  # a cancel stopped it
    park: Park | None = None  # the loop cannot go on with it


class LoopManagerImpl(LoopManagerInterface):
    def __init__(
        self,
        steps: StepsManagerInterface,
        sessions: AgentSessionsManagerInterface,
        agents: AgentsManagerInterface,
        kinds: AgentKindCatalog,
        attribution: AttributionManagerInterface,
        models: ModelsManagerInterface,
        windows: WindowsManagerInterface,
        tools: ToolsManagerInterface,
        gate: CallGateInterface,
        providers: ModelProvidersInterface,
        outages: OutageSignalInterface,
        sink: StreamSinkInterface,
        catalog: Sequence[ToolInterface],
        options: LoopOptions,
        clock: Callable[[], datetime] = utcnow,
        sleep: Sleep = asyncio.sleep,
        *,
        domain_classes: Sequence[str] = (),
    ) -> None:
        self._steps = steps
        self._sessions = sessions
        self._agents = agents
        self._kinds = kinds
        self._attribution = attribution
        self._models = models
        self._windows = windows
        self._tools = tools
        self._gate = gate
        self._providers = providers
        self._outages = outages
        self._sink = sink
        self._catalog = tuple(catalog)
        self._domain_classes = tuple(domain_classes)
        self._options = options
        self._clock = clock
        self._sleep = sleep

    # A run.

    async def run(self, ctx: TenantContext, session_id: UUID) -> LoopRun:
        ctx.require(Permission.WRITE)
        # The epoch first, before the history is read: from here on a run
        # that held an earlier one writes nothing.
        epoch = await self._steps.begin_run(ctx, session_id)
        try:
            return await self._run(ctx, session_id, epoch)
        except StaleWriter:
            log.info("loop run of session %s at epoch %d lost its claim", session_id, epoch)
            return LoopRun(session_id=session_id, epoch=epoch, end=RunEnd.STALE)

    async def _run(self, ctx: TenantContext, session_id: UUID, epoch: int) -> LoopRun:
        session = await self._sessions.project_status(ctx, session_id)
        idle = LoopRun(session_id=session_id, epoch=epoch, end=RunEnd.IDLE)
        if session.status in (SessionStatus.IDLE, SessionStatus.PARKED):
            return idle
        history = await self._history(ctx, session_id)
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
                # never ran; a hand-over was written from outside, while a
                # call may have been running, and is settled by effect.
                session = await self._sessions.resume(ctx, session_id, epoch, loop_id)
                resumed = loop.park.reason is not ParkReason.HANDOVER
        kind = self._kinds.get(session.kind, session.kind_version)
        registry = ToolRegistry(
            (tool for tool in self._catalog if tool.spec.name in session.tools),
            self._domain_classes,
        )
        tree = await self._agents.tree_of(ctx, session_id)
        run = _Run(
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
        )
        try:
            await self._models.resolve_fill_set(ctx, session_id, kind.roles, Eligibility())
        except (UnresolvedRole, UnpricedModel) as refused:
            log.warning("session %s resolves no fill: %s", session_id, refused.message)
            return await self._end(run, LoopOutcome.ERRORED)
        try:
            # Before the first model call: a workspace weaker than the spec
            # is never made, and nothing is spent on a loop that cannot run.
            run.workspace = await self._tools.prepare_workspace(ctx, session_id, kind.isolation)
        except InfraException as refused:
            # A provider that cannot meet the spec refuses it whole: no
            # weaker workspace, and no call spent on a loop that cannot run.
            if refused.code != ISOLATION_REFUSED:
                raise
            log.warning("session %s has no workspace: %s", session_id, refused)
            return await self._end(run, LoopOutcome.ERRORED)
        try:
            return await self._drive(run, history)
        except StaleWriter:
            # The claim is another run's, or a person's who took the
            # environment over: what it holds is theirs to release.
            run.workspace = None
            raise
        finally:
            await self._release(run)

    async def _drive(self, run: _Run, history: Sequence[Step]) -> LoopRun:
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
            history = await self._history(run.ctx, run.session_id)
            loop = rules.OpenLoop(run.loop_id, run.start_seq, None)
            if rules.asked(history, loop, ControlCommand.CANCEL) is not None:
                return await self._cancelled(run)
            if rules.pause_waits(history, loop):
                return await self._park(run, Park(reason=ParkReason.PAUSE, unlock="resume"))
            response = rules.latest_response(history, run.loop_id)
            if response is not None:
                calls = rules.open_calls(history, response)
                if calls:
                    stopped = await self._tool_turn(run, response, calls)
                    if stopped is not None:
                        return stopped
                    continue
                accepted = rules.accepted_outcome(history, response)
                if accepted is not None:
                    # The result gate accepted a result this turn submitted,
                    # and every call of the turn is answered.
                    return await self._end(run, accepted)
                if not response.as_tool_uses() and not rules.judged(history, response):
                    stopped = await self._judge(run, history, response)
                    if stopped is not None:
                        return stopped
                    continue
            trip = tripped(
                run.kind.limits,
                tally_loop(history, run.loop_id),
                now=self._clock(),
                run_started_at=run.started_at,
                deadline=run.deadline,
            )
            if trip is not None:
                if trip.outcome is not None:
                    return await self._end(run, trip.outcome)
                if trip.park is not None:
                    return await self._park(run, trip.park)
                return self._result(run, RunEnd.YIELDED)
            stopped = await self._model_turn(run)
            if stopped is not None:
                return stopped

    # A turn of the model.

    async def _model_turn(self, run: _Run) -> LoopRun | None:
        ctx = run.ctx
        fill_set = await self._models.get_fill_set(ctx, run.session_id)
        fill = fill_set.fill_for(MAIN)
        if fill is None:
            return await self._end(run, LoopOutcome.ERRORED)
        # A provider known to be failing for this credential parks the loop
        # at once, before anything is rendered, held, or spent.
        outage = await self._outages.current(
            fill.provider.value, self._options.credential, self._clock()
        )
        if outage is not None:
            return await self._park(run, provider_park(fill, outage.retry_at))
        prompts = rules.kind_prompts(run.kind, run.registry)
        try:
            if run.refused is not None:
                rendered = await self._windows.render_after_overflow(
                    ctx, run.session_id, run.epoch, run.loop_id, prompts, run.refused
                )
            else:
                rendered = await self._windows.render_request(
                    ctx, run.session_id, run.epoch, run.loop_id, prompts, MAIN
                )
        except ModelCallFailed as failed:
            # The compaction's call to the summarizer failed: its own
            # provider's error, which no switch of the main role mends.
            summarizer = fill_set.fill_for(SUMMARIZER) or fill
            return await self._failed(run, summarizer, None, failed)
        except PlatformException as refused:
            return await self._refused(run, refused)
        try:
            replied = await self._call(run, fill, rendered)
        except ModelCallFailed as failed:
            return await self._failed(run, fill, rendered, failed)
        except PlatformException as refused:
            return await self._refused(run, refused)
        run.failures, run.refused = 0, None
        run.tried.clear()
        header = replied.header
        if (
            isinstance(header, ModelResponseHeader)
            and header.stop_reason is StopReason.OUTPUT_LIMIT
        ):
            # Cut by its output bound: kept truncated and never acted on,
            # and the model is told so, so the next request is not this one
            # again.
            await self._notice(run, rules.CUT)
        return None

    async def _refused(self, run: _Run, refused: PlatformException) -> LoopRun:
        """What stops a model turn before its call is made: the gate's refusal
        parks on the budget, nobody to pay parks for a person, and a window
        that cannot be read ends the loop. Anything else is not the loop's to
        answer."""
        match refused:
            case BudgetRefused():
                return await self._park(run, budget_park(refused.refusal, self._clock()))
            case NoSpender():
                park = Park(reason=ParkReason.PERSON, unlock=rules.SPENDER_UNLOCK)
                return await self._park(run, park)
            case CompactionFailed() | ContextOverflow() | Unavailable():
                log.warning("session %s: %s", run.session_id, refused.message)
                return await self._end(run, LoopOutcome.ERRORED)
            case _:
                raise refused

    async def _call(self, run: _Run, fill: Fill, rendered: RenderedRequest) -> Step:
        """One model call: who spoke and who pays, the gate's hold, the request
        persisted, the stream emitted as it arrives, and the response
        persisted before anything acts on it."""
        ctx, session_id = run.ctx, run.session_id
        said = rendered.attribution
        hold = await self._gate.authorize(ctx, session_id, said.spender, MAIN, fill, rendered.call)
        request = request_step(
            rendered, said, session_id, run.loop_id, new_id(), self._clock(), hold_id=hold
        )
        try:
            (request,) = await self._steps.append_steps(ctx, session_id, run.epoch, [request])
        except BaseException:
            # Never sent: nothing was billed.
            await self._gate.settle(ctx, hold, None, billed=False)
            raise
        response_id = new_id()
        try:
            reply = await self._stream(run, fill, rendered.call, response_id)
        except ModelCallFailed as failed:
            # Nothing streamed back: the call was refused before it was
            # processed, and the hold is released. A stream that broke is
            # usually billed, so it counts whole, and what arrived is kept.
            await self._gate.settle(ctx, hold, None, billed=failed.partial is not None)
            closing = (
                rules.abandoned_step(response_id, self._clock(), request)
                if failed.partial is None
                else rules.response_step(response_id, self._clock(), request, failed.partial)
            )
            await self._steps.append_steps(ctx, session_id, run.epoch, [closing])
            raise
        except BaseException:
            with contextlib.suppress(Exception):
                await self._gate.settle(ctx, hold, None, billed=True)
            raise
        await self._gate.settle(ctx, hold, reply.usage, billed=True)
        stored = rules.response_step(response_id, self._clock(), request, reply)
        (stored,) = await self._steps.append_steps(ctx, session_id, run.epoch, [stored])
        return stored

    async def _stream(
        self, run: _Run, fill: Fill, call: ModelCall, response_id: UUID
    ) -> ModelReply:
        """The provider's stream, each part emitted as it arrives, never
        waited on; the reply is the one its last part carries."""
        numbers = itertools.count()
        async for part in self._providers.get(fill.provider).stream(call):
            if isinstance(part, Finished):
                return part.reply
            emitted = rules.stream_part(run.session_id, response_id, next(numbers), part)
            if emitted is not None:
                self._sink.emit(emitted)
        raise ModelCallFailed(ErrorKind.TRANSIENT, "the stream ended with no reply")

    async def _failed(
        self, run: _Run, fill: Fill, rendered: RenderedRequest | None, failed: ModelCallFailed
    ) -> LoopRun | None:
        """A provider error, handled by its kind. `rendered` is the main
        request that failed, or None when the compaction's call did, which
        neither falls back nor compacts again. None goes on to the next model
        turn."""
        answer = failed.kind.answer
        now = self._clock()
        main = rendered is not None
        if answer is ErrorAnswer.RETRY:
            wait = rules.retry_wait(failed.retry_after, run.failures, self._options.retry_base)
            run.failures += 1
            if run.failures <= self._options.retries and wait <= self._options.retry_cap:
                await self._sleep(wait.total_seconds())
                return None
            # The retries are spent: the provider is failing for this
            # credential. Every session that would call it learns so, and
            # parks at once until the retry time; this one falls back to its
            # next declared fallback when it has one, and parks too when not.
            retry_at = now + max(wait, self._options.outage_wait)
            outage = Outage(
                provider=fill.provider.value,
                credential=self._options.credential,
                kind=failed.kind.value,
                retry_at=retry_at,
            )
            await self._outages.report(outage, now)
            if main and await self._fall_back(run, fill):
                return None
            return await self._park(run, provider_park(fill, retry_at))
        if answer is ErrorAnswer.COMPACT:
            if rendered is None or rendered.overflow_retry:
                return await self._end(run, LoopOutcome.ERRORED)
            run.refused = rendered
            return None
        if answer is ErrorAnswer.RESOLVE:
            if main and await self._fall_back(run, fill):
                return None
            return await self._end(run, LoopOutcome.ERRORED)
        if answer is ErrorAnswer.PARK:
            unlock = f"{fill.provider.value}:{failed.kind.value}"
            return await self._park(run, Park(reason=ParkReason.PROVIDER, unlock=unlock))
        log.warning("session %s: %s", run.session_id, failed)
        return await self._end(run, LoopOutcome.ERRORED)

    async def _fall_back(self, run: _Run, fill: Fill) -> bool:
        """A switch of the main role to its next declared fallback the session
        admits and no call of this run has tried; False when none is left."""
        switched = await self._models.fall_back(
            run.ctx, run.session_id, run.epoch, run.loop_id, MAIN, (*run.tried, fill)
        )
        if switched is None:
            return False
        run.tried.append(fill)
        run.failures = 0
        return True

    async def _judge(self, run: _Run, history: Sequence[Step], response: Step) -> LoopRun | None:
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

    async def _notice(self, run: _Run, text: str) -> None:
        principal = await self._attribution.call_principal(run.ctx, run.session_id)
        step = rules.notice_step(
            new_id(), self._clock(), run.session_id, run.loop_id, principal, text
        )
        await self._steps.append_steps(run.ctx, run.session_id, run.epoch, [step])

    # The tool calls a response asked for.

    async def _tool_turn(
        self, run: _Run, response: Step, calls: Sequence[tuple[ToolUseBlock, Step | None]]
    ) -> LoopRun | None:
        """Every open call of a response: its request persisted first, then
        each one gated, run or recovered, and answered, in the order the
        model made them. A call that waits for a person parks the loop once
        the others are answered."""
        requests = await self._requests(run, response, calls)
        asked = False
        for use, request in requests:
            settled = await self._settle_call(
                run, use, request, fresh=self._never_ran(run, request)
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

    def _never_ran(self, run: _Run, request: Step) -> bool:
        """Whether no run may have started a call: this run wrote it, or a
        park the loop wrote held it back. A run settles a lost run's calls
        before it parks, so a call still open at such a park is one it
        never started."""
        return request.id in run.made or run.resumed

    async def _recover_calls(self, run: _Run) -> LoopRun | None:
        """The calls a lost run left open, each settled by its effect before
        the loop does anything else: none of them is ever run as new. A call
        that would wait for a person, or whose principal lapsed, is answered
        as stopped with its outcome unknown, rather than parked open. A
        cancel that waits answers them all so."""
        history = await self._history(run.ctx, run.session_id)
        response = rules.latest_response(history, run.loop_id)
        if response is None:
            return None
        lost = [(use, request) for use, request in rules.open_calls(history, response) if request]
        if not lost:
            return None
        loop = rules.OpenLoop(run.loop_id, run.start_seq, None)
        if rules.asked(history, loop, ControlCommand.CANCEL) is not None:
            return await self._cancelled(run)
        for use, request in lost:
            settled = await self._settle_call(run, use, request, fresh=False)
            if settled.cancelled:
                return await self._cancelled(run)
        return None

    async def _requests(
        self, run: _Run, response: Step, calls: Sequence[tuple[ToolUseBlock, Step | None]]
    ) -> list[tuple[ToolUseBlock, Step]]:
        """The request of every call, writing those not yet written, in one
        append, before any is decided; the run keeps the ids it wrote."""
        missing = [use for use, request in calls if request is None]
        if not missing:
            return [(use, request) for use, request in calls if request is not None]
        principal = await self._attribution.call_principal(run.ctx, run.session_id)
        agent = AgentRef(kind=run.kind.name, version=run.kind.version, session_id=run.session_id)
        now = self._clock()
        built = [
            tool_request(
                new_id(),
                now,
                response,
                use,
                self._class_of(run, use.name),
                principal=principal,
                authority=run.kind.authority,
                agent=agent,
            )
            for use in missing
        ]
        stored = await self._steps.append_steps(run.ctx, run.session_id, run.epoch, built)
        by_use = {_use_id(step): step for step in stored}
        run.made.update(step.id for step in stored)
        return [(use, request or by_use[use.id]) for use, request in calls]

    async def _settle_call(
        self, run: _Run, use: ToolUseBlock, request: Step, *, fresh: bool
    ) -> _Settled:
        ctx = run.ctx
        assert run.workspace is not None
        try:
            gate = await self._tools.gate(
                ctx,
                run.registry,
                run.kind.policy,
                request,
                use.input,
                run.workspace,
                holds_private=rules.holds_private(run.kind, run.registry),
                tree_deadline=run.deadline,
            )
        except PrincipalLapsed:
            if not fresh:
                await self._answer(run, request, self._lost(request))
                return _Settled()
            return _Settled(park=Park(reason=ParkReason.PERSON, unlock=rules.PRINCIPAL_UNLOCK))
        if gate.outcome is GateOutcome.REFUSE and gate.response is not None:
            await self._answer(run, request, gate.response)
            return _Settled()
        if gate.outcome is GateOutcome.ASK or gate.authority is None:
            if not fresh:
                # It may have run once; it never waits open for a second.
                await self._answer(run, request, self._lost(request))
                return _Settled()
            return _Settled(asked=True)
        if use.name == run.kind.result_tool:
            return await self._submit(run, request, use)
        tool = run.registry.get(use.name)
        assert tool is not None  # the gate resolved it
        if tool.spec.mode is ToolMode.JOB:
            await self._answer(run, request, self._stopped(request, NO_JOBS, ToolFailure.PERMANENT))
            return _Settled()
        # The call runs under the live context of the principal attribution
        # answered for it, asked again for this call.
        call_ctx = gate.authority.context
        on_output = self._output(run, request)
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
            await self._answer(run, request, self._stopped(request, text, ToolFailure.INTERRUPTED))
            return _Settled(cancelled=ran is ControlCommand.CANCEL)
        await self._answer(run, request, ran)
        return _Settled()

    async def _execute(
        self,
        run: _Run,
        call_ctx: TenantContext,
        tool: ToolInterface,
        request: Step,
        call_input: Mapping[str, Any],
        on_output: OutputSink,
    ) -> Step | ControlCommand:
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

    async def _submit(self, run: _Run, request: Step, use: ToolUseBlock) -> _Settled:
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
            return _Settled()
        verdict = await self._agents.judge_result(run.ctx, run.session_id, result)
        if not verdict.accepted or verdict.outcome is None:
            reason = verdict.reason or "the result gate refused the result"
            await self._answer(run, request, self._stopped(request, reason, ToolFailure.DENIED))
            return _Settled()
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
        return _Settled()

    async def _answer(self, run: _Run, request: Step, answer: Step) -> None:
        """A call's response under the id this run gives it, kept whole as an
        artifact when it is too large for a step, and persisted before the
        next model call. A run that lost its claim is refused here, whatever
        it answered."""
        own = answer.model_copy(update={"id": response_id(request, run.epoch)})
        bounded = await self._windows.bound_tool_response(run.ctx, run.session_id, own)
        await self._steps.append_steps(run.ctx, run.session_id, run.epoch, [bounded])

    def _lost(self, request: Step) -> Step:
        text = "a run that made this call was lost before it answered; whether it ran is unknown"
        return self._stopped(request, text, ToolFailure.INTERRUPTED)

    def _stopped(self, request: Step, text: str, failure: ToolFailure) -> Step:
        return response(
            new_id(),
            self._clock(),
            request,
            text,
            failure,
            limit=self._options.stopped_chars,
        )

    def _output(self, run: _Run, request: Step) -> OutputSink:
        """Where a running call's output streams: numbered parts of the
        response the call adds up to, emitted and never waited on."""
        step_id = response_id(request, run.epoch)
        numbers = itertools.count()

        async def on_output(channel: str, text: str) -> None:
            self._sink.emit(
                ToolOutputPart(
                    session_id=run.session_id,
                    step_id=step_id,
                    n=next(numbers),
                    channel=channel,
                    text=text,
                )
            )

        return on_output

    def _class_of(self, run: _Run, name: str) -> str:
        """A call's class, as its tool declares it. A name the registry does
        not hold is refused by the gate before any class is read."""
        tool = run.registry.get(name)
        return tool.spec.authorization_class if tool is not None else "unknown"

    # How a run stops.

    async def _cancelled(self, run: _Run) -> LoopRun:
        """A principal's cancel: every call still open is answered as stopped,
        so the history holds no open call, and the loop ends `cancelled`,
        and so do its children's."""
        history = await self._history(run.ctx, run.session_id)
        response = rules.latest_response(history, run.loop_id)
        if response is not None:
            calls = rules.open_calls(history, response)
            if calls:
                requests = await self._requests(run, response, calls)
                answers = [
                    self._cancelled_call(run, request).model_copy(
                        update={"id": response_id(request, run.epoch)}
                    )
                    for _, request in requests
                ]
                await self._steps.append_steps(run.ctx, run.session_id, run.epoch, answers)
        return await self._end(run, LoopOutcome.CANCELLED)

    def _cancelled_call(self, run: _Run, request: Step) -> Step:
        """A cancel's answer to a call still open: stopped before it ran, or,
        for one a lost run may have started, stopped with its outcome
        unknown, so the model verifies before it calls again."""
        if self._never_ran(run, request):
            text = f"stopped by a principal's {ControlCommand.CANCEL.value} before it ran"
            return self._stopped(request, text, ToolFailure.INTERRUPTED)
        return self._lost(request)

    async def _end(self, run: _Run, outcome: LoopOutcome) -> LoopRun:
        step = rules.ended_step(new_id(), self._clock(), run.session_id, run.loop_id, outcome)
        await self._steps.append_steps(run.ctx, run.session_id, run.epoch, [step])
        await self._sessions.project_status(run.ctx, run.session_id)
        if outcome is LoopOutcome.CANCELLED:
            await self._agents.cancel_children(run.ctx, run.session_id)
        return self._result(run, RunEnd.ENDED, outcome=outcome)

    async def _park(self, run: _Run, park: Park) -> LoopRun:
        await self._sessions.park(run.ctx, run.session_id, run.epoch, run.loop_id, park)
        return self._result(run, RunEnd.PARKED, park=park)

    def _result(
        self,
        run: _Run,
        end: RunEnd,
        *,
        outcome: LoopOutcome | None = None,
        park: Park | None = None,
    ) -> LoopRun:
        return LoopRun(
            session_id=run.session_id,
            epoch=run.epoch,
            loop_id=run.loop_id,
            end=end,
            outcome=outcome,
            park=park,
        )

    async def _close_lost(self, run: _Run, request: Step) -> None:
        header = request.header
        hold = header.hold_id if isinstance(header, ModelRequestHeader) else None
        if hold is not None:
            with contextlib.suppress(NotFound):
                await self._gate.settle(run.ctx, hold, None, billed=True)
        closing = rules.abandoned_step(new_id(), self._clock(), request)
        await self._steps.append_steps(run.ctx, run.session_id, run.epoch, [closing])

    async def _release(self, run: _Run) -> None:
        """The workspace's instance goes between runs; its files stay. A loop
        that parks holds no runtime."""
        if run.workspace is None:
            return
        try:
            await self._tools.release_workspace(run.ctx, run.workspace)
        except Exception:
            log.exception("session %s: the workspace was not released", run.session_id)

    # Taking over.

    async def take_over(self, ctx: TenantContext, session_id: UUID) -> AgentSession:
        ctx.require(Permission.WRITE)
        epoch = await self._steps.begin_run(ctx, session_id)
        history = await self._history(ctx, session_id)
        loop = rules.open_loop(history)
        loop_id = new_id() if loop is None else loop.loop_id
        park = Park(reason=ParkReason.HANDOVER, unlock=HANDOVER_UNLOCK)
        return await self._sessions.park(ctx, session_id, epoch, loop_id, park)

    async def give_back(self, ctx: TenantContext, session_id: UUID, summary: str) -> AgentSession:
        ctx.require(Permission.WRITE)
        session = await self._sessions.project_status(ctx, session_id)
        if session.park is None or session.park.reason is not ParkReason.HANDOVER:
            raise ValidationFailed(f"agent session {session_id} is not handed over")
        epoch = await self._steps.begin_run(ctx, session_id)
        loop = rules.open_loop(await self._history(ctx, session_id))
        if loop is None:
            raise ValidationFailed(f"agent session {session_id} holds no loop to give back")
        now = self._clock()
        changed = rules.changed_step(new_id(), now, session_id, loop.loop_id)
        await self._steps.append_steps(ctx, session_id, epoch, [changed])
        message_id = new_id()
        message = Step(
            id=message_id,
            created_at=now,
            session_id=session_id,
            loop_id=message_id,
            type=StepType.MESSAGE,
            actor=Actor.PERSON,
            origin=origin_of(ctx.app.type),
            header=InputHeader(
                waking=True, principal=Principal(kind=PrincipalKind.PERSON, id=ctx.user_id)
            ),
            content=Content(blocks=(TextBlock(text=summary),)),
        )
        unlock = unlock_step(new_id(), session_id, now)
        await self._steps.append_inputs(ctx, session_id, [message, unlock])
        return await self._sessions.project_status(ctx, session_id)

    # The history.

    async def _history(self, ctx: TenantContext, session_id: UUID) -> list[Step]:
        steps: list[Step] = []
        while True:
            after = steps[-1].seq if steps else 0
            page = await self._steps.get_steps(ctx, session_id, after, self._options.page)
            steps.extend(page.items)
            if not page.has_more or not page.items:
                return steps

    async def _since(self, run: _Run, seq: int) -> list[Step]:
        page = await self._steps.get_steps(run.ctx, run.session_id, seq, self._options.page)
        return list(page.items)


def provider_park(fill: Fill, retry_at: datetime) -> Park:
    """The park of a provider that is failing: it names the provider and
    tries again by itself at the retry time."""
    return Park(reason=ParkReason.PROVIDER, unlock=fill.provider.value, retry_at=retry_at)


def _use_id(request: Step) -> str:
    header = request.header
    return getattr(header, "tool_use_id", "")
