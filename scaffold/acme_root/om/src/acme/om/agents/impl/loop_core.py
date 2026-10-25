"""What every concern of the loop shares: its options, the state of one
run, and the base each concern extends, which holds the managers the
loop was built with and the seams between the concerns."""

import asyncio
import logging
import random
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import Field

from acme.infra.agent_workspaces import (
    AgentWorkspace,
    Durability,
)
from acme.infra.outages import OutageSignalInterface
from acme.om.agent_sessions import AgentSessionsManagerInterface
from acme.om.agent_sessions.types.agent_session import AgentSession
from acme.om.agent_steps import AgentStepsManagerInterface
from acme.om.agent_steps.types.agent_step import AgentStep
from acme.om.agent_steps.types.content import ToolUseBlock
from acme.om.agent_steps.types.header import (
    LoopOutcome,
    Park,
    ToolFailure,
)
from acme.om.agent_steps.types.stream import StreamPart
from acme.om.agents import AgentsManagerInterface
from acme.om.agents import loop_rules as rules
from acme.om.agents.hooks import (
    NO_ATTACHED_TOOLSETS,
    AttachedToolsets,
    LoopHooksInterface,
)
from acme.om.agents.sink import StreamSinkInterface
from acme.om.agents.types.kind import AgentKind, AgentKindCatalog
from acme.om.agents.types.run import LoopRun, RunEnd
from acme.om.attribution import AttributionManagerInterface
from acme.om.base import OmBase, new_id, utcnow
from acme.om.context import TenantContext
from acme.om.exceptions import (
    NotFound,
    StaleWriter,
)
from acme.om.language_models.credentials import CallCredentialsInterface
from acme.om.language_models.manager import LanguageModelsManagerInterface
from acme.om.language_models.types.fill import Fill
from acme.om.leases import LeasesManagerInterface
from acme.om.leases.types.request import Standing
from acme.om.tools import ToolsManagerInterface
from acme.om.tools.native.load_toolset import callable_tools, loads
from acme.om.tools.registry import ToolRegistry
from acme.om.tools.rules import (
    response,
    response_id,
)
from acme.om.tools.tool import ToolInterface
from acme.om.tools.types.policy import ToolPolicyLayer
from acme.om.windows import WindowsManagerInterface
from acme.om.windows.gate import CallGateInterface
from acme.om.windows.types.window import RenderedRequest

log = logging.getLogger(__name__)

Sleep = Callable[[float], Awaitable[None]]
"""How the loop waits, injected beside its clock, so a test never sleeps."""


class LoopOptions(OmBase):
    # The name of the platform's own key, as the outage signal keeps it,
    # which a root gives the call credentials it wires.
    credential: str = Field(default="platform", min_length=1)
    retries: int = Field(default=2, ge=0)  # in-process retries of an error worth retrying
    # The first backoff, doubled after each, half of it fixed and half drawn
    # at random, so sessions that failed together do not ask again together.
    retry_base: timedelta = timedelta(seconds=1)
    # A wait longer than this is not spent in process: the loop falls back,
    # or parks on the provider.
    retry_cap: timedelta = timedelta(seconds=30)
    # The least time an outage is marked for when the loop parks on it.
    outage_wait: timedelta = timedelta(seconds=30)
    # How long a park on a key or an account a provider refused waits before
    # it tries again by itself: a person fixes it at the provider, or in the
    # platform's own settings, and nothing tells the engine.
    fix_wait: timedelta = timedelta(minutes=5)
    # How often a running tool's controls are read: a cancel or an interrupt
    # stops it within this.
    control_poll: timedelta = timedelta(milliseconds=500)
    page: int = Field(default=200, gt=0)  # steps one read of the history asks for
    stopped_chars: int = Field(default=2_000, gt=0)  # the most a stopped call's answer holds
    # The same call failing this many times in a row, and each multiple of
    # it, earns the model a notice to change its approach.
    repeats_noticed: int = Field(default=3, ge=1)
    # How long a loop no host can give its workspace waits, parked on the
    # resource, before it asks again.
    agent_workspace_wait: timedelta = timedelta(minutes=1)


@dataclass
class RunState:
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
    # The policy layers of the kinds above the session in its tree, nearest
    # first: each of its calls is decided under them as under its own kind's.
    above: tuple[ToolPolicyLayer, ...] = ()
    workspace: AgentWorkspace | None = None
    kept: bool = False  # a snapshot holds its workspace whole, taken at its end
    made: set[UUID] = field(default_factory=lambda: set[UUID]())  # tool requests it wrote
    failures: int = 0  # provider errors in a row
    tried: list[Fill] = field(default_factory=lambda: [])
    refused: RenderedRequest | None = None  # a request the provider refused as too long
    sink_failed: bool = False  # the stream sink refused a part; it is logged once
    # The jobs the loop parked on whose calls were open when the run began,
    # by key: whatever the run has not answered goes with its loop's end.
    jobs: dict[UUID, rules.StartedJob] = field(default_factory=lambda: {})
    # What the session holds beyond its kind, folded into `kind` already.
    attached: AttachedToolsets = NO_ATTACHED_TOOLSETS
    # The steps the run appended, by seq: its working state, with the
    # history it holds (ADR 1042). A read of the history joins each where it
    # falls, so the run reads back nothing it wrote, only what others wrote
    # between.
    own: dict[int, AgentStep] = field(default_factory=lambda: {})

    @property
    def agent_session_id(self) -> UUID:
        return self.session.id


@dataclass
class Settled:
    """How one tool call stood once the loop dealt with it."""

    asked: bool = False  # it waits for a person's decision
    cancelled: bool = False  # a cancel stopped it
    park: Park | None = None  # the loop cannot go on with it


@dataclass
class Heard:
    """What a loop's asks in line came to once the run read them."""

    waiting: list[Standing]  # the asks that still wait, each with its place
    told: bool = False  # it wrote a notice the next request reads


class LoopCore(ABC):
    """What every concern of a run reads: the managers, the clock, and the
    helpers they share. `LoopManagerImpl` is the concerns together."""

    def __init__(
        self,
        steps: AgentStepsManagerInterface,
        sessions: AgentSessionsManagerInterface,
        agents: AgentsManagerInterface,
        kinds: AgentKindCatalog,
        attribution: AttributionManagerInterface,
        models: LanguageModelsManagerInterface,
        windows: WindowsManagerInterface,
        tools: ToolsManagerInterface,
        gate: CallGateInterface,
        credentials: CallCredentialsInterface,
        outages: OutageSignalInterface,
        sink: StreamSinkInterface,
        catalog: Sequence[ToolInterface],
        options: LoopOptions,
        clock: Callable[[], datetime] = utcnow,
        sleep: Sleep = asyncio.sleep,
        *,
        leases: LeasesManagerInterface,
        domain_classes: Sequence[str] = (),
        hooks: LoopHooksInterface,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        """`credentials` answers the client each call runs on and the
        credential it carries: the platform's key, or a tenant's. `leases`
        answers where each request a tool asked in line stands. `hooks` is
        where a layer above extends each run: a session's attached toolsets,
        and the steps it appends before the run's first call."""
        self._hooks = hooks
        self._jitter = jitter
        self._leases = leases
        self._steps = steps
        self._sessions = sessions
        self._agents = agents
        self._kinds = kinds
        self._attribution = attribution
        self._language_models = models
        self._windows = windows
        self._tools = tools
        self._gate = gate
        self._credentials = credentials
        self._outages = outages
        self._sink = sink
        self._catalog = tuple(catalog)
        self._domain_classes = tuple(domain_classes)
        self._options = options
        self._clock = clock
        self._sleep = sleep

    async def _history(
        self,
        ctx: TenantContext,
        agent_session_id: UUID,
        read: Sequence[AgentStep] = (),
        *,
        since: int = 0,
        own: dict[int, AgentStep] | None = None,
    ) -> list[AgentStep]:
        """The session's whole history, in `seq` order: `read`, what was read
        of it already, and every step after it; with nothing read, every
        step after `since`. `own` holds steps the run appended, by seq: each
        joins where it falls, and only the steps between them are read. An
        append takes the next seqs and commits in their order, so the steps
        between are those others wrote first; a read that finds fewer reads
        on from storage alone, as if the run had appended nothing."""
        steps = list(read)
        held = {} if own is None else own
        while True:
            after = steps[-1].seq if steps else since
            while after + 1 in held:
                after += 1
                steps.append(held[after])
            ahead = min((seq for seq in held if seq > after), default=None)
            limit = (
                self._options.page if ahead is None else min(self._options.page, ahead - after - 1)
            )
            page = await self._steps.get_agent_steps(ctx, agent_session_id, after, limit)
            steps.extend(page.items)
            if ahead is None:
                if not page.has_more or not page.items:
                    return steps
            elif len(page.items) < limit:
                held = {}

    async def _append(self, run: RunState, steps: Sequence[AgentStep]) -> tuple[AgentStep, ...]:
        """An append under the run's epoch. What it stored joins the run's
        history at its next read, with no read back."""
        stored = await self._steps.append_agent_steps(
            run.ctx, run.agent_session_id, run.epoch, steps
        )
        run.own.update((step.seq, step) for step in stored)
        return stored

    async def _since(self, run: RunState, seq: int) -> list[AgentStep]:
        page = await self._steps.get_agent_steps(
            run.ctx, run.agent_session_id, seq, self._options.page
        )
        return list(page.items)

    def _emit(self, run: RunState, part: StreamPart) -> None:
        """Hands a part to the sink. A sink that fails costs the live view,
        never the call it watches: the step still holds everything."""
        try:
            self._sink.emit(part)
        except Exception:
            self._sink_failed(run)

    def _tell(
        self, run: RunState, tell: Callable[[TenantContext, UUID, UUID], None], agent_step_id: UUID
    ) -> None:
        """Tells the sink a stream opened or completed, as `_emit` hands it a
        part: a sink that fails costs the live view alone."""
        try:
            tell(run.ctx, run.agent_session_id, agent_step_id)
        except Exception:
            self._sink_failed(run)

    def _sink_failed(self, run: RunState) -> None:
        if not run.sink_failed:
            run.sink_failed = True
            log.warning("session %s: the stream sink failed", run.agent_session_id, exc_info=True)

    async def _answer(
        self, run: RunState, request: AgentStep, answer: AgentStep, *after: AgentStep
    ) -> tuple[AgentStep, ...]:
        """A call's response under the id this run gives it, kept whole as a
        tool output when it is too large for a step, and persisted before the
        next model call, with the steps `after` it in the same append. A
        run that lost its claim is refused here, whatever it answered."""
        own = answer.model_copy(update={"id": response_id(request, run.epoch)})
        bounded = await self._windows.bound_tool_response(run.ctx, run.agent_session_id, own)
        return await self._append(run, [bounded, *after])

    def _stopped(self, request: AgentStep, text: str, failure: ToolFailure) -> AgentStep:
        return response(
            new_id(),
            self._clock(),
            request,
            text,
            failure,
            limit=self._options.stopped_chars,
        )

    def _lost(self, request: AgentStep) -> AgentStep:
        text = "a run that made this call was lost before it answered; whether it ran is unknown"
        return self._stopped(request, text, ToolFailure.INTERRUPTED)

    def _never_ran(self, run: RunState, request: AgentStep) -> bool:
        """Whether no run may have started a call: this run wrote it, or a
        park the loop wrote held it back. A run settles a lost run's calls
        before it parks, or marks the park unsettled, so a call still open
        at a settled park is one it never started."""
        return request.id in run.made or run.resumed

    async def _notice(self, run: RunState, text: str) -> None:
        principal = await self._attribution.call_principal(run.ctx, run.agent_session_id)
        step = rules.notice_step(
            new_id(), self._clock(), run.agent_session_id, run.loop_id, principal, text
        )
        await self._append(run, [step])

    def _registry(
        self,
        session: AgentSession,
        kind: AgentKind,
        history: Sequence[AgentStep],
        attached: AttachedToolsets,
    ) -> ToolRegistry:
        """The run's registry: the tools of the catalog, and those attached
        to the session, that it may call and that wait for no load, and those
        the history's loads added."""
        holds = (*session.tools, *attached.names)
        names = callable_tools(holds, kind.waiting, loads(history))
        return ToolRegistry(
            (tool for tool in (*self._catalog, *attached.tools) if tool.spec.name in names),
            self._domain_classes,
        )

    def _result(
        self,
        run: RunState,
        end: RunEnd,
        *,
        outcome: LoopOutcome | None = None,
        park: Park | None = None,
    ) -> LoopRun:
        return LoopRun(
            agent_session_id=run.agent_session_id,
            epoch=run.epoch,
            loop_id=run.loop_id,
            end=end,
            outcome=outcome,
            park=park,
        )

    async def _standing(self, run: RunState, request_id: UUID) -> Standing | None:
        """Where a request stands, or None once it is past its retention."""
        try:
            return await self._leases.get_request(run.ctx, request_id)
        except NotFound:
            return None

    async def _keep(self, run: RunState) -> None:
        """A workspace kept by snapshots is snapshotted whole at the end of
        each run that held it, live, before its loop parks or ends: no run
        that follows starts before the step names it. One that is refused,
        such as for a secret's value in it, a secret an attached tool may
        have injected included, or that fails keeps nothing, and the run
        keeps its instance (`_release`)."""
        workspace = run.workspace
        if workspace is None or workspace.spec.durability is not Durability.SNAPSHOT:
            return
        try:
            await self._tools.snapshot_agent_workspace(
                run.ctx,
                run.agent_session_id,
                workspace,
                epoch=run.epoch,
                loop_id=run.loop_id,
                tools=run.attached.tools,
            )
        except StaleWriter:
            raise
        except Exception:
            log.exception("session %s: the workspace was not snapshotted", run.agent_session_id)
            return
        run.kept = True

    async def _release(self, run: RunState) -> None:
        """The workspace's instance goes between runs; its files stay. A loop
        that parks holds no runtime, but for a workspace kept by snapshots
        that no snapshot holds whole: its instance stays, so nothing in it is
        lost, and the next run prepares it as it stands."""
        if run.workspace is None:
            return
        if run.workspace.spec.durability is Durability.SNAPSHOT and not run.kept:
            log.warning(
                "session %s keeps its workspace live: no snapshot holds it", run.agent_session_id
            )
            return
        try:
            await self._tools.release_agent_workspace(run.ctx, run.workspace)
        except Exception:
            log.exception("session %s: the workspace was not released", run.agent_session_id)

    async def _report(
        self, run: RunState, *, outcome: LoopOutcome | None = None, park: Park | None = None
    ) -> None:
        """A child tells its parent how its loop stands, read from the loop's
        own steps: from its first, never the session's whole history."""
        steps = await self._history(run.ctx, run.agent_session_id, since=run.start_seq - 1)
        loop = rules.OpenLoop(run.loop_id, run.start_seq, None)
        report = rules.report_of(steps, loop, outcome=outcome, park=park)
        await self._agents.report_to_parent(run.ctx, run.agent_session_id, report)

    # The seams between the concerns: each is one concern's, and the others
    # call it through here.

    @abstractmethod
    async def _end(
        self, run: RunState, outcome: LoopOutcome, *, cause: str | None = None
    ) -> LoopRun: ...

    @abstractmethod
    async def _park(self, run: RunState, park: Park) -> LoopRun: ...

    @abstractmethod
    async def _cancelled(self, run: RunState, park: Park | None = None) -> LoopRun: ...

    @abstractmethod
    async def _requests(
        self,
        run: RunState,
        response: AgentStep,
        calls: Sequence[tuple[ToolUseBlock, AgentStep | None]],
    ) -> list[tuple[ToolUseBlock, AgentStep]]: ...

    @abstractmethod
    async def _settle_call(
        self,
        run: RunState,
        history: Sequence[AgentStep],
        use: ToolUseBlock,
        request: AgentStep,
        *,
        fresh: bool,
    ) -> Settled: ...

    @abstractmethod
    def _offered(self, run: RunState, request: AgentStep) -> ToolInterface | None: ...

    @abstractmethod
    async def _stop_jobs(
        self, run: RunState, jobs: Sequence[rules.StartedJob], why: str
    ) -> None: ...
