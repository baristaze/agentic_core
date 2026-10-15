from collections.abc import Callable, Collection, Sequence
from datetime import datetime
from uuid import UUID

from acme.om.agent_steps import AgentStepsManagerInterface
from acme.om.agent_steps.types.agent_step import Actor, AgentStep, AgentStepType, Origin
from acme.om.agent_steps.types.header import SwitchedHeader
from acme.om.base import OmBase, new_id, utcnow
from acme.om.context import Permission, TenantContext
from acme.om.exceptions import NotFound, PreconditionFailed, ValidationFailed
from acme.om.language_models import rules
from acme.om.language_models.manager import LanguageModelsManagerInterface
from acme.om.language_models.resolver import ModelResolverInterface
from acme.om.language_models.storage import FillSetStorageInterface
from acme.om.language_models.types.fill import (
    Eligibility,
    Fill,
    FillSet,
    FillSwitch,
    ModelAsk,
    ModelRole,
    SwitchReason,
)
from acme.om.tenancy import TenancyManagerInterface


class LanguageModelsOptions(OmBase):
    purge_batch: int = 1000  # fill-set versions one purge statement deletes at most


class LanguageModelsManagerImpl(LanguageModelsManagerInterface):
    def __init__(
        self,
        storage: FillSetStorageInterface,
        steps: AgentStepsManagerInterface,
        tenancy: TenancyManagerInterface,
        resolver: ModelResolverInterface,
        options: LanguageModelsOptions,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._storage = storage
        self._steps = steps
        self._tenancy = tenancy
        self._resolver = resolver
        self._options = options
        self._clock = clock

    async def resolve_fill_set(
        self,
        ctx: TenantContext,
        agent_session_id: UUID,
        roles: Sequence[ModelRole],
        eligibility: Eligibility,
        asks: Sequence[ModelAsk] = (),
    ) -> FillSet:
        ctx.require(Permission.WRITE)
        head = await self._storage.read_fill_set(ctx.org_id, agent_session_id, None)
        if head is not None:
            return head
        first = FillSet(
            id=new_id(),
            created_at=self._clock(),
            agent_session_id=agent_session_id,
            version=1,
            roles=await self._resolver.resolve(ctx, roles, eligibility, asks),
            eligibility=eligibility,
        )
        try:
            await self._storage.write_fill_set(ctx.org_id, first)
        except PreconditionFailed:
            # A resolution that raced this one wrote the first version: the
            # session keeps that one.
            return await self._head(ctx, agent_session_id)
        return first

    async def get_fill_set(self, ctx: TenantContext, agent_session_id: UUID) -> FillSet:
        ctx.require(Permission.READ)
        return await self._head(ctx, agent_session_id)

    async def switch_fill(
        self,
        ctx: TenantContext,
        agent_session_id: UUID,
        epoch: int,
        loop_id: UUID,
        role: ModelRole,
        to: Fill,
        reason: SwitchReason,
    ) -> FillSet:
        ctx.require(Permission.WRITE)
        head = await self._head(ctx, agent_session_id)
        refusal = rules.switch_refusal(head, role, to)
        if refusal is not None:
            raise ValidationFailed(refusal)
        self._resolver.check(to)
        fills = rules.fill_switch(head, role, to, reason)
        step = AgentStep(
            id=new_id(),
            created_at=self._clock(),
            agent_session_id=agent_session_id,
            loop_id=loop_id,
            type=AgentStepType.SWITCHED,
            actor=Actor.ENGINE,
            origin=Origin.ENGINE,
            header=SwitchedHeader(fills=fills),
        )
        # The history first: a run that lost its claim is refused here, and
        # nothing changes. The version follows the step it announces.
        (stored,) = await self._steps.append_agent_steps(ctx, agent_session_id, epoch, [step])
        return await self._record(ctx, head, stored.id, fills, stored.created_at)

    async def fall_back(
        self,
        ctx: TenantContext,
        agent_session_id: UUID,
        epoch: int,
        loop_id: UUID,
        role: ModelRole,
        tried: Collection[Fill] = (),
    ) -> FillSet | None:
        ctx.require(Permission.WRITE)
        head = await self._head(ctx, agent_session_id)
        to = rules.next_fallback(head, role, tried)
        if to is None:
            return None
        return await self.switch_fill(
            ctx, agent_session_id, epoch, loop_id, role, to, SwitchReason.FALLBACK
        )

    async def settle_switch(
        self,
        ctx: TenantContext,
        agent_session_id: UUID,
        agent_step_id: UUID,
        fills: FillSwitch,
        at: datetime,
    ) -> FillSet:
        ctx.require(Permission.WRITE)
        version = fills.fill_set_version
        stored = await self._storage.read_fill_set(ctx.org_id, agent_session_id, version)
        if stored is not None:
            if stored.switched_by != agent_step_id:
                raise PreconditionFailed(
                    f"version {version} is another switch's than {agent_step_id}"
                )
            return stored
        head = await self._head(ctx, agent_session_id)
        if head.version != version - 1:
            raise PreconditionFailed(
                f"step {agent_step_id} announces version {version}, and the head is {head.version}"
            )
        return await self._record(ctx, head, agent_step_id, fills, at)

    async def purge_tenant(self, ctx: TenantContext) -> int:
        ctx.require(Permission.WRITE)
        if not await self._tenancy.tenant_expired(ctx):
            return 0
        return await self._storage.purge_tenant(ctx.org_id, self._options.purge_batch)

    async def _head(self, ctx: TenantContext, agent_session_id: UUID) -> FillSet:
        head = await self._storage.read_fill_set(ctx.org_id, agent_session_id, None)
        if head is None:
            raise NotFound(f"session {agent_session_id} has no fill set")
        return head

    async def _record(
        self,
        ctx: TenantContext,
        head: FillSet,
        agent_step_id: UUID,
        fills: FillSwitch,
        at: datetime,
    ) -> FillSet:
        """Writes the version the step `agent_step_id` announces over `head`; one
        written before is answered as stored, since its id is the step's."""
        version = rules.switched(head, fills, agent_step_id, at)
        await self._storage.write_fill_set(ctx.org_id, version)
        return version
