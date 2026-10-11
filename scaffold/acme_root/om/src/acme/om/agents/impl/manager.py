import contextlib
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from uuid import UUID

from acme.infra.workspaces import IsolationMode
from acme.om.agent_sessions import AgentSessionsManagerInterface
from acme.om.agent_sessions.limits import deadline_park
from acme.om.agent_sessions.types.agent_session import AgentSession, SessionStatus
from acme.om.agents.gate import ResultGateInterface
from acme.om.agents.manager import AgentsManagerInterface
from acme.om.agents.rules import (
    CHILDREN_PARK,
    claim_refusal,
    notes_parent,
    report_text,
    report_wakes,
    tree_for,
    tree_refusal,
)
from acme.om.agents.storage import AgentStorageInterface
from acme.om.agents.types.kind import AgentKind, AgentKindCatalog
from acme.om.agents.types.report import Report
from acme.om.agents.types.request import Handoff, Spawn, Start
from acme.om.agents.types.result import Result, Verdict
from acme.om.agents.types.tree import AgentTree
from acme.om.attribution import AttributionManagerInterface
from acme.om.attribution.types.authority import SessionAuthority
from acme.om.attribution.types.principal import AgentRef, Principal, PrincipalKind
from acme.om.base import Platform, derived_id, new_id, utcnow
from acme.om.budgets import BudgetsManagerInterface
from acme.om.context import Permission, TenantContext
from acme.om.exceptions import NotAuthorized, NotFound, TreeBoundReached, ValidationFailed
from acme.om.outbox import OutboxRelayInterface
from acme.om.outbox.types.row import OutboxRow, versioned_row
from acme.om.steps import StepsManagerInterface
from acme.om.steps.types.content import Content, TextBlock
from acme.om.steps.types.header import (
    ControlCommand,
    ControlHeader,
    InputHeader,
    WorkspaceSnapshot,
)
from acme.om.steps.types.step import Actor, Origin, Step, StepType
from acme.om.tenancy import TenancyManagerInterface
from acme.om.tools import ToolsManagerInterface
from acme.om.tools.rules import instruct_refusal
from acme.om.tools.tool import TakenSnapshot, TakeSnapshot
from acme.om.windows import WindowsManagerInterface

CREATED = "agents.agent_tree.created"
UPDATED = "agents.agent_tree.updated"


class AgentsOptions(Platform):
    max_limit: int = 50  # children one read of the cascade holds
    purge_batch: int = 1000  # trees one purge statement deletes at most


class AgentsManagerImpl(AgentsManagerInterface):
    def __init__(
        self,
        storage: AgentStorageInterface,
        sessions: AgentSessionsManagerInterface,
        steps: StepsManagerInterface,
        attribution: AttributionManagerInterface,
        gate: ResultGateInterface,
        kinds: AgentKindCatalog,
        tenancy: TenancyManagerInterface,
        relay: OutboxRelayInterface,
        options: AgentsOptions,
        clock: Callable[[], datetime] = utcnow,
        *,
        budgets: BudgetsManagerInterface,
        windows: WindowsManagerInterface,
        tool_classes: Mapping[str, str],
        secret_tools: frozenset[str],
        tools: ToolsManagerInterface,
    ) -> None:
        self._tools = tools
        self._budgets = budgets
        self._windows = windows
        self._tool_classes = tool_classes
        self._secret_tools = secret_tools
        self._storage = storage
        self._sessions = sessions
        self._steps = steps
        self._attribution = attribution
        self._gate = gate
        self._kinds = kinds
        self._tenancy = tenancy
        self._relay = relay
        self._options = options
        self._clock = clock

    async def start_session(self, ctx: TenantContext, start: Start) -> AgentSession:
        ctx.require(Permission.WRITE)
        kind = self._kinds.latest(start.kind)
        self._may_instruct(ctx, kind.tools)
        # The tree first: a session never stands without the tree it draws on.
        await self._create_tree(
            ctx, tree_for(kind, start.id, start.deadline, self._clock(), ctx.user_id)
        )
        session = self._session(ctx, kind, start.id, start.title, start.participants)
        created = await self._sessions.create_session(ctx, session)
        await self._open(ctx, created)
        return created

    async def require_instructor(self, ctx: TenantContext, session_id: UUID) -> None:
        ctx.require(Permission.WRITE)
        session = await self._find(ctx, session_id)
        if session is not None:
            self._may_instruct(ctx, session.tools)

    async def spawn(
        self, ctx: TenantContext, parent_id: UUID, spawn: Spawn, fork: TakeSnapshot | None = None
    ) -> AgentSession:
        ctx.require(Permission.WRITE)
        parent = await self._sessions.get_session(ctx, parent_id)
        child = await self._find(ctx, spawn.id)
        if child is not None and child.parent_id != parent_id:
            raise ValidationFailed(f"agent session {spawn.id} is not a child of {parent_id}")
        taken: TakenSnapshot | None = None
        if child is not None:
            self._may_instruct(ctx, child.tools)
        else:
            kind = self._kinds.latest(spawn.kind)
            if kind.result_tool is not None and kind.result_tool not in parent.tools:
                # Its tools are cut to its parent's, so it could never submit.
                raise ValidationFailed(f"agent session {parent_id} cannot grant {kind.result_tool}")
            if kind.share is None:
                # With no cap of its own, one child could spend all its tree has left.
                raise ValidationFailed(f"agent kind {kind.name} names no share to spawn it under")
            if fork is not None and kind.isolation.mode is IsolationMode.NONE:
                raise ValidationFailed(f"agent kind {kind.name} has no workspace to fork into")
            # Its objective instructs it: whoever spawns it may make every
            # call it will offer, asked before anything is made.
            self._may_instruct(ctx, [tool for tool in kind.tools if tool in parent.tools])
            tree = await self._tree(ctx, parent.root_id)
            refusal = tree_refusal(tree, parent.depth + 1)
            if refusal is not None:
                raise TreeBoundReached(refusal)
            # The parent's workspace as it stands is taken before anything of
            # the tree is spent: one refused takes no slot and makes no child.
            taken = None if fork is None else await fork()
            # The slot is taken before the child is made: a crash between the
            # two leaves the count one high, never one low.
            if await self._storage.take_slot(ctx.org_id, tree.id) is None:
                raise TreeBoundReached(f"the tree holds its {tree.count} sub-agents")
            # The mark, the cut of the tools, and the depth are the create's,
            # and the principal and the spender the authority's, each taken
            # from the parent.
            made = self._session(ctx, kind, spawn.id, spawn.title, (), parent_id)
            child = await self._sessions.create_session(ctx, made)
        share = self._kinds.get(child.kind, child.kind_version).share
        if share is not None:
            # Its share is its own budget, written before the objective wakes
            # it; a retry answers the one written. Every call of it still
            # passes the tree's budget, so the share never adds to it.
            cap_id = derived_id(child.id, child.created_at, "share")
            await self._budgets.cap_session(ctx, child.id, cap_id, share)
        authority = await self._open(ctx, child)
        objective = self._input(
            child,
            authority.principal,
            derived_id(child.id, child.created_at, "objective"),
            from_agent=parent,
            origin=Origin.PARENT,
            text=spawn.objective,
            waking=True,
            untrusted=child.untrusted,
        )
        arrivals = [objective]
        if fork is not None:
            # Its workspace starts from its own copy of its parent's as the
            # spawn took it, so what it writes never reaches its parent's:
            # the restore comes before the objective that wakes it. A retry
            # makes the same step, which the inbox answers as stored.
            copy = await self._fork_copy(ctx, child, fork, taken)
            restore_id = derived_id(child.id, child.created_at, "fork")
            restore = Step(
                id=restore_id,
                created_at=self._clock(),
                session_id=child.id,
                loop_id=restore_id,
                type=StepType.CONTROL,
                actor=Actor.ENGINE,
                origin=Origin.PARENT,
                header=ControlHeader(command=ControlCommand.RESTORE, snapshot=copy),
            )
            arrivals = [restore, objective]
        # Through the inbox: the projection that turns the child pending
        # asks for its loop's run.
        _, child = await self._sessions.receive(ctx, child.id, arrivals)
        return child

    async def _fork_copy(
        self,
        ctx: TenantContext,
        child: AgentSession,
        fork: TakeSnapshot,
        taken: TakenSnapshot | None,
    ) -> WorkspaceSnapshot:
        """The child's copy of its parent's workspace, under an id its
        spawn derives. A spawn asked again answers the copy the child's
        history already names; one that never got so far takes the
        workspace as it stands now."""
        snapshot_id = derived_id(child.id, child.created_at, "fork-snapshot")
        if taken is None:
            try:
                return await self._tools.find_snapshot(ctx, child.id, snapshot_id)
            except NotFound:
                taken = await fork()
        return await self._tools.fork_snapshot(ctx, child.id, snapshot_id, taken)

    async def tree_of(self, ctx: TenantContext, session_id: UUID) -> AgentTree:
        ctx.require(Permission.READ)
        session = await self._sessions.get_session(ctx, session_id)
        return await self._tree(ctx, session.root_id)

    async def set_deadline(
        self, ctx: TenantContext, session_id: UUID, deadline: datetime | None
    ) -> AgentTree:
        ctx.require(Permission.WRITE)
        tree = await self.tree_of(ctx, session_id)
        moved = AgentTree.model_validate(
            {
                **tree.model_dump(),
                "deadline": deadline,
                "version": tree.version + 1,
                "updated_at": self._clock(),
                "updated_by": ctx.user_id,
            }
        )
        rows = (versioned_row(ctx, UPDATED, moved.id, moved.version),)
        await self._storage.write_tree(ctx.org_id, moved, tree.version, rows)
        await self._relay_all(ctx, rows)
        if deadline is None or deadline > self._clock():
            # The extension is the deadline park's unlock: a session of the
            # tree that waits on it goes on, and its gates run again. A
            # member marked deleted waits on nothing, and the rest go on.
            for member in (tree.id, *await self._below(ctx, tree.id)):
                with contextlib.suppress(NotFound):
                    await self._sessions.wake_session(ctx, member, deadline_park())
        return moved

    async def cancel_children(self, ctx: TenantContext, session_id: UUID) -> tuple[UUID, ...]:
        ctx.require(Permission.WRITE)
        below = await self._below(ctx, session_id)
        return tuple([child for child in below if await self._cancel(ctx, child)])

    async def report_to_parent(
        self, ctx: TenantContext, session_id: UUID, report: Report
    ) -> Step | None:
        ctx.require(Permission.WRITE)
        if report.park is not None and not notes_parent(report.park):
            return None
        # The mark and the private data as the child's history stands now:
        # data it read, or a report it took, since its cached status was
        # folded count too.
        child = await self._sessions.get_session_at_head(ctx, session_id)
        if child.parent_id is None:
            return None
        parent = await self._find(ctx, child.parent_id)
        if parent is None:
            return None
        principal = await self._attribution.call_principal(ctx, child.id)
        if report.outcome is not None:
            # One end a loop: a run that ends it again writes this report once.
            step_id = derived_id(report.loop_id, child.created_at, "report")
        else:
            step_id = new_id()
        # The child's words are data in its parent: an agent's message, never
        # its parent's instruction, and it marks the parent as data does.
        # What the child holds private, its parent holds once it reads them.
        step = self._input(
            parent,
            principal,
            step_id,
            from_agent=child,
            origin=Origin.ENGINE,
            text=report_text(child, report),
            waking=report_wakes(report),
            untrusted=child.untrusted,
            holds_private=child.holds_private,
        )
        bounded = await self._windows.bound_report(ctx, parent.id, step)
        # Through the inbox: the projection that turns the parent pending
        # asks for its loop's run.
        (stored,), parent = await self._sessions.receive(ctx, parent.id, [bounded])
        if report_wakes(report) and parent.park == CHILDREN_PARK:
            # A parent that waits on its children waits for this report: it
            # clears the park, and the parent's gates run again as it resumes.
            await self._sessions.wake_session(ctx, parent.id, CHILDREN_PARK)
        return stored

    async def hand_off(
        self, ctx: TenantContext, session_id: UUID, handoff: Handoff
    ) -> AgentSession:
        ctx.require(Permission.WRITE)
        source = await self._sessions.get_session(ctx, session_id)
        session = await self._find(ctx, handoff.id)
        if session is not None and session.handed_off_from != session_id:
            raise ValidationFailed(
                f"agent session {handoff.id} was not handed over by {session_id}"
            )
        if session is None:
            kind = self._kinds.latest(handoff.kind)
            await self._create_tree(
                ctx, tree_for(kind, handoff.id, None, self._clock(), ctx.user_id)
            )
            principal = await self._attribution.call_principal(ctx, session_id)
            participants = (principal.id,) if principal.kind is PrincipalKind.PERSON else ()
            made = self._session(
                ctx, kind, handoff.id, handoff.title, participants, handed_off_from=session_id
            )
            session = await self._sessions.create_session(ctx, made)
        authority = await self._open(ctx, session)
        # The objective is the agent's, so it is data and wakes nothing: the
        # session starts when its principal speaks.
        objective = self._input(
            session,
            authority.principal,
            derived_id(session.id, session.created_at, "objective"),
            from_agent=source,
            origin=Origin.ENGINE,
            text=handoff.objective,
            waking=False,
            untrusted=session.untrusted,
        )
        await self._steps.append_inputs(ctx, session.id, [objective])
        return session

    async def judge_result(self, ctx: TenantContext, session_id: UUID, result: Result) -> Verdict:
        ctx.require(Permission.READ)
        await self._sessions.get_session(ctx, session_id)
        refusal = claim_refusal(result)
        if refusal is not None:
            return Verdict(accepted=False, reason=refusal)
        return await self._gate.check(ctx, session_id, result)

    async def purge_tree(self, org_id: UUID, tree_id: UUID) -> bool:
        return await self._storage.purge_tree(org_id, tree_id)

    async def purge_tenant(self, ctx: TenantContext) -> int:
        ctx.require(Permission.WRITE)
        if not await self._tenancy.tenant_expired(ctx):
            return 0
        return await self._storage.purge_tenant(ctx.org_id, self._options.purge_batch)

    def _may_instruct(self, ctx: TenantContext, tools: Iterable[str]) -> None:
        """The classes a registry offers are what its sender must be able to
        make. A name the catalog cannot class offers a call no one can
        check, so it is refused, never skipped."""
        names = tuple(tools)
        unknown = sorted(name for name in names if name not in self._tool_classes)
        if unknown:
            raise NotAuthorized(f"no tool of the catalog classes {', '.join(unknown)}")
        refusal = instruct_refusal(ctx, [self._tool_classes[name] for name in names])
        if refusal is not None:
            raise NotAuthorized(refusal)

    def _session(
        self,
        ctx: TenantContext,
        kind: AgentKind,
        session_id: UUID,
        title: str,
        participants: tuple[UUID, ...],
        parent_id: UUID | None = None,
        *,
        handed_off_from: UUID | None = None,
    ) -> AgentSession:
        """A session as its maker sends it: the kind pinned at its version,
        its tools, and whether they hold private data: its kind says so, or
        one of its tools is given a secret. What it takes from where it came
        from is the create's to set."""
        now = self._clock()
        return AgentSession(
            id=session_id,
            created_at=now,
            updated_at=now,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
            title=title,
            participants=participants,
            kind=kind.name,
            kind_version=kind.version,
            tools=kind.tools,
            holds_private=kind.private_data or not self._secret_tools.isdisjoint(kind.tools),
            parent_id=parent_id,
            root_id=session_id,
            handed_off_from=handed_off_from,
        )

    async def _open(self, ctx: TenantContext, session: AgentSession) -> SessionAuthority:
        """The session's authority, in the mode of the kind version it
        pinned; made once, and answered as stored after."""
        mode = self._kinds.get(session.kind, session.kind_version).authority
        return await self._attribution.open_authority(ctx, session.id, mode)

    def _input(
        self,
        session: AgentSession,
        principal: Principal,
        step_id: UUID,
        *,
        from_agent: AgentSession,
        origin: Origin,
        text: str,
        waking: bool,
        untrusted: bool,
        holds_private: bool = False,
    ) -> Step:
        """A message an agent writes into `session`: on the authority
        `principal`, naming the agent that wrote it, and carrying the mark of
        the session it came from, `untrusted`, and, for a report, whether
        that session holds private data, `holds_private`."""
        return Step(
            id=step_id,
            created_at=self._clock(),
            session_id=session.id,
            loop_id=step_id,
            type=StepType.MESSAGE,
            actor=Actor.AGENT,
            origin=origin,
            header=InputHeader(
                waking=waking,
                principal=principal,
                agent=AgentRef(
                    kind=from_agent.kind,
                    version=from_agent.kind_version,
                    session_id=from_agent.id,
                ),
                untrusted=untrusted,
                holds_private=holds_private,
            ),
            content=Content(blocks=(TextBlock(text=text),)),
        )

    async def _cancel(self, ctx: TenantContext, session_id: UUID) -> bool:
        """A `cancel` control on the loop the session has open, or is about
        to open, decided from its status brought up to its history: an idle
        session has none and is left as it is, and so is one marked deleted,
        which was idle when it was marked. A session pending after its loop
        ended waits on an input, which begins the next loop."""
        try:
            session = await self._sessions.project_status(ctx, session_id)
        except NotFound:
            return False
        cursor = await self._steps.get_cursor(ctx, session_id)
        if session.status is SessionStatus.IDLE or cursor.head == 0:
            return False
        (last,) = (await self._steps.get_steps(ctx, session_id, cursor.head - 1, 1)).items
        loop_id = last.loop_id
        if last.type is StepType.LOOP_ENDED and session.pending_input is not None:
            loop_id = session.pending_input
        control = Step(
            id=new_id(),
            created_at=self._clock(),
            session_id=session_id,
            loop_id=loop_id,
            type=StepType.CONTROL,
            actor=Actor.ENGINE,
            origin=Origin.PARENT,
            header=ControlHeader(command=ControlCommand.CANCEL),
        )
        # Through the inbox: a parked child the cancel clears turns pending,
        # and its loop's run is asked for to end it.
        await self._sessions.receive(ctx, session_id, [control])
        return True

    async def _below(self, ctx: TenantContext, session_id: UUID) -> list[UUID]:
        """Every session below `session_id`, children and theirs, a level at
        a time; the tree's count bounds the walk."""
        below: list[UUID] = []
        parents = [session_id]
        while parents:
            parent_id = parents.pop(0)
            after: UUID | None = None
            while True:
                page = await self._sessions.get_children(
                    ctx, parent_id, after, self._options.max_limit
                )
                for child in page.items:
                    parents.append(child.id)
                    below.append(child.id)
                if not page.has_more or not page.items:
                    break
                after = page.items[-1].id
        return below

    async def _find(self, ctx: TenantContext, session_id: UUID) -> AgentSession | None:
        try:
            return await self._sessions.get_session(ctx, session_id)
        except NotFound:
            return None

    async def _tree(self, ctx: TenantContext, tree_id: UUID) -> AgentTree:
        tree = await self._storage.read_tree(ctx.org_id, tree_id)
        if tree is None:
            raise NotFound(f"agent tree {tree_id} not found")
        return tree

    async def _create_tree(self, ctx: TenantContext, tree: AgentTree) -> None:
        """The create, announced; a tree written already is a retry, and is
        kept as it is."""
        rows = (versioned_row(ctx, CREATED, tree.id, tree.version),)
        if await self._storage.create_tree(ctx.org_id, tree, rows):
            await self._relay_all(ctx, rows)

    async def _relay_all(self, ctx: TenantContext, rows: tuple[OutboxRow, ...]) -> None:
        """The write has committed; a relay that fails is left to the sweep."""
        if rows:
            await self._relay.relay_all(ctx.org_id, rows)
