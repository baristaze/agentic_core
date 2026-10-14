from collections.abc import Sequence
from uuid import UUID

from acme.om.agent_steps.manager import (
    AgentStepsManagerInterface,
    InstructCheck,
    OpenCheck,
    SteerCheck,
)
from acme.om.agent_steps.storage import AgentStepStorageInterface
from acme.om.agent_steps.types.agent_step import AgentStep, switches_standing
from acme.om.agent_steps.types.page import AgentStepCursor, AgentStepPage
from acme.om.attribution.rules import (
    decided_by,
    instructs,
    person_written,
    principal_of,
    said_by,
    switched_by,
)
from acme.om.base import Platform
from acme.om.context import Permission, TenantContext
from acme.om.exceptions import NotAuthorized, ValidationFailed
from acme.om.tenancy import TenancyManagerInterface


class AgentStepsOptions(Platform):
    max_limit: int = 200  # steps one page holds at most
    max_append: int = 100  # steps one append writes at most
    purge_batch: int = 1000  # steps one purge statement deletes at most


async def no_registry(ctx: TenantContext, agent_session_id: UUID) -> None:
    """The check of a history no agent reads: no session over it offers a
    tool, so anyone who may write it may speak in it."""


async def no_steerer(ctx: TenantContext, agent_session_id: UUID) -> bool:
    """The check of a history no session stands over: whoever may write it
    steers it."""
    return True


async def no_session(ctx: TenantContext, agent_session_id: UUID) -> None:
    """The check of a history no session stands over: whoever may read it
    opens it."""


class AgentStepsManagerImpl(AgentStepsManagerInterface):
    def __init__(
        self,
        storage: AgentStepStorageInterface,
        tenancy: TenancyManagerInterface,
        options: AgentStepsOptions,
        *,
        instructs: InstructCheck,
        steers: SteerCheck,
        opens: OpenCheck,
    ) -> None:
        self._storage = storage
        self._tenancy = tenancy
        self._options = options
        self._instructs = instructs
        self._steers = steers
        self._opens = opens

    async def begin_run(self, ctx: TenantContext, agent_session_id: UUID) -> int:
        ctx.require(Permission.WRITE)
        return await self._storage.begin_run(ctx.org_id, agent_session_id)

    async def append_agent_steps(
        self, ctx: TenantContext, agent_session_id: UUID, epoch: int, steps: Sequence[AgentStep]
    ) -> tuple[AgentStep, ...]:
        ctx.require(Permission.WRITE)
        self._bound(steps)
        said = await self._said(ctx, agent_session_id, steps)
        await self._asked(ctx, agent_session_id, said)
        return await self._storage.append_agent_steps(ctx.org_id, agent_session_id, epoch, said)

    async def append_inputs(
        self, ctx: TenantContext, agent_session_id: UUID, steps: Sequence[AgentStep]
    ) -> tuple[AgentStep, ...]:
        ctx.require(Permission.WRITE)
        self._bound(steps)
        said = await self._said(ctx, agent_session_id, steps)
        await self._asked(ctx, agent_session_id, said)
        return await self._storage.append_inputs(ctx.org_id, agent_session_id, said)

    async def get_agent_steps(
        self, ctx: TenantContext, agent_session_id: UUID, after_seq: int, limit: int
    ) -> AgentStepPage:
        ctx.require(Permission.READ)
        if not ctx.has(Permission.MANAGE_AGENT_SESSIONS):
            await self._opens(ctx, agent_session_id)
        limit = max(1, min(limit, self._options.max_limit))
        rows = await self._storage.read_agent_steps(
            ctx.org_id, agent_session_id, max(0, after_seq), limit + 1
        )
        return AgentStepPage(items=tuple(rows[:limit]), has_more=len(rows) > limit)

    async def get_cursor(self, ctx: TenantContext, agent_session_id: UUID) -> AgentStepCursor:
        ctx.require(Permission.READ)
        return await self._storage.read_cursor(ctx.org_id, agent_session_id)

    async def purge_tenant(self, ctx: TenantContext) -> int:
        ctx.require(Permission.WRITE)
        if not await self._tenancy.tenant_expired(ctx):
            return 0
        return await self._storage.purge_tenant(ctx.org_id, self._options.purge_batch)

    async def purge_histories(
        self, sessions: Sequence[tuple[UUID, UUID]]
    ) -> list[tuple[UUID, UUID]]:
        batch = self._options.purge_batch
        gone: list[tuple[UUID, UUID]] = []
        for org_id, agent_session_id in sessions:
            if await self._storage.purge_history(org_id, agent_session_id, batch) < batch:
                gone.append((org_id, agent_session_id))
        return gone

    async def _asked(
        self, ctx: TenantContext, agent_session_id: UUID, steps: Sequence[AgentStep]
    ) -> None:
        """An append that holds an instruction asks first whether its sender
        may instruct the session, whichever append carries it."""
        if any(instructs(step) for step in steps):
            await self._instructs(ctx, agent_session_id)

    async def _said(
        self, ctx: TenantContext, agent_session_id: UUID, steps: Sequence[AgentStep]
    ) -> list[AgentStep]:
        """Each step in the name of the context that appends it: a person's
        message is its user's, the steerer's an instruction and a
        participant's data; a decision its user's, in its role; and a switch
        of the steerer or of who sees the session its user's. Only the
        steerer pins standing text."""
        speaker = principal_of(ctx)
        steers = True
        if any(person_written(step) or switches_standing(step) for step in steps):
            steers = await self._steers(ctx, agent_session_id)
        if not steers and any(switches_standing(step) for step in steps):
            raise NotAuthorized(
                f"only the steerer of agent session {agent_session_id} pins its text"
            )
        return [
            switched_by(
                decided_by(said_by(step, speaker, steers=steers), ctx.user_id, ctx.role), speaker
            )
            for step in steps
        ]

    def _bound(self, steps: Sequence[AgentStep]) -> None:
        if len(steps) > self._options.max_append:
            raise ValidationFailed(f"an append holds at most {self._options.max_append} steps")
