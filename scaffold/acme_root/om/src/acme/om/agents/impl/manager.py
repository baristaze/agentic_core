from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from acme.om.agent_sessions import AgentSessionsManagerInterface
from acme.om.agent_sessions.types.agent_session import AgentSession
from acme.om.agents.gate import ResultGateInterface
from acme.om.agents.manager import AgentsManagerInterface
from acme.om.agents.rules import claim_refusal, tree_for, tree_refusal
from acme.om.agents.storage import AgentStorageInterface
from acme.om.agents.types.kind import AgentKind, AgentKindCatalog
from acme.om.agents.types.request import Handoff, Spawn, Start
from acme.om.agents.types.result import Result, Verdict
from acme.om.agents.types.tree import AgentTree
from acme.om.attribution import AttributionManagerInterface
from acme.om.attribution.types.authority import SessionAuthority
from acme.om.attribution.types.principal import AgentRef, Principal, PrincipalKind
from acme.om.base import Platform, derived_id, new_id, utcnow
from acme.om.context import Permission, TenantContext
from acme.om.exceptions import NotFound, TreeBoundReached, ValidationFailed
from acme.om.outbox import OutboxRelayInterface
from acme.om.outbox.types.row import OutboxRow, versioned_row
from acme.om.steps import StepsManagerInterface
from acme.om.steps.types.content import Content, TextBlock
from acme.om.steps.types.header import ControlCommand, ControlHeader, InputHeader
from acme.om.steps.types.step import Actor, Origin, Step, StepType
from acme.om.tenancy import TenancyManagerInterface

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
    ) -> None:
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
        # The tree first: a session never stands without the tree it draws on.
        await self._create_tree(
            ctx, tree_for(kind, start.id, start.deadline, self._clock(), ctx.user_id)
        )
        session = self._session(ctx, kind, start.id, start.title, start.participants)
        created = await self._sessions.create_session(ctx, session)
        await self._open(ctx, created)
        return created

    async def spawn(self, ctx: TenantContext, parent_id: UUID, spawn: Spawn) -> AgentSession:
        ctx.require(Permission.WRITE)
        parent = await self._sessions.get_session(ctx, parent_id)
        child = await self._find(ctx, spawn.id)
        if child is not None and child.parent_id != parent_id:
            raise ValidationFailed(f"agent session {spawn.id} is not a child of {parent_id}")
        if child is None:
            kind = self._kinds.latest(spawn.kind)
            if kind.result_tool is not None and kind.result_tool not in parent.tools:
                # Its tools are cut to its parent's, so it could never submit.
                raise ValidationFailed(f"agent session {parent_id} cannot grant {kind.result_tool}")
            tree = await self._tree(ctx, parent.root_id)
            refusal = tree_refusal(tree, parent.depth + 1)
            if refusal is not None:
                raise TreeBoundReached(refusal)
            # The slot is taken before the child is made: a crash between the
            # two leaves the count one high, never one low.
            if await self._storage.take_slot(ctx.org_id, tree.id) is None:
                raise TreeBoundReached(f"the tree holds its {tree.count} sub-agents")
            # The mark, the cut of the tools, and the depth are the create's,
            # and the principal and the spender the authority's, each taken
            # from the parent.
            made = self._session(ctx, kind, spawn.id, spawn.title, (), parent_id)
            child = await self._sessions.create_session(ctx, made)
        authority = await self._open(ctx, child)
        objective = self._input(
            child,
            authority.principal,
            derived_id(child.id, child.created_at, "objective"),
            from_agent=parent,
            origin=Origin.PARENT,
            text=spawn.objective,
            waking=True,
        )
        await self._steps.append_inputs(ctx, child.id, [objective])
        return child

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
        return moved

    async def cancel_children(self, ctx: TenantContext, session_id: UUID) -> tuple[UUID, ...]:
        ctx.require(Permission.WRITE)
        reached: list[UUID] = []
        parents = [session_id]
        # Down the tree a level at a time; the tree's count bounds the walk.
        while parents:
            parent_id = parents.pop(0)
            after: UUID | None = None
            while True:
                page = await self._sessions.get_children(
                    ctx, parent_id, after, self._options.max_limit
                )
                for child in page.items:
                    parents.append(child.id)
                    if await self._cancel(ctx, child.id):
                        reached.append(child.id)
                if not page.has_more or not page.items:
                    break
                after = page.items[-1].id
        return tuple(reached)

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

    async def purge_tenant(self, ctx: TenantContext) -> int:
        ctx.require(Permission.WRITE)
        if not await self._tenancy.tenant_expired(ctx):
            return 0
        return await self._storage.purge_tenant(ctx.org_id, self._options.purge_batch)

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
        and its tools. What it takes from where it came from is the
        create's to set."""
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
    ) -> Step:
        """A message an agent writes into `session`: on the authority
        `session` runs under, `principal`, naming the agent that wrote it,
        and carrying the mark `session` took from it."""
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
                untrusted=session.untrusted,
            ),
            content=Content(blocks=(TextBlock(text=text),)),
        )

    async def _cancel(self, ctx: TenantContext, session_id: UUID) -> bool:
        """A `cancel` control on the session's open loop, or nothing when no
        loop is open: no history yet, or its last loop ended."""
        cursor = await self._steps.get_cursor(ctx, session_id)
        if cursor.head == 0:
            return False
        page = await self._steps.get_steps(ctx, session_id, cursor.head - 1, 1)
        if not page.items or page.items[0].type is StepType.LOOP_ENDED:
            return False
        control = Step(
            id=new_id(),
            created_at=self._clock(),
            session_id=session_id,
            loop_id=page.items[0].loop_id,
            type=StepType.CONTROL,
            actor=Actor.ENGINE,
            origin=Origin.PARENT,
            header=ControlHeader(command=ControlCommand.CANCEL),
        )
        await self._steps.append_inputs(ctx, session_id, [control])
        return True

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
