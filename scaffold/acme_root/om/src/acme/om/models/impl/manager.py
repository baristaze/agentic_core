from collections.abc import Collection, Sequence
from uuid import UUID

from acme.om.base import new_id, utcnow
from acme.om.context import Permission, TenantContext
from acme.om.exceptions import NotFound, PreconditionFailed, ValidationFailed
from acme.om.models import rules
from acme.om.models.manager import ModelsManagerInterface
from acme.om.models.resolver import ModelResolverInterface
from acme.om.models.storage import FillSetStorageInterface
from acme.om.models.types.fill import Eligibility, Fill, FillSet, ModelRole, SwitchReason
from acme.om.steps import StepsManagerInterface
from acme.om.steps.types.header import SwitchedHeader
from acme.om.steps.types.step import Actor, Origin, Step, StepType


class ModelsManagerImpl(ModelsManagerInterface):
    def __init__(
        self,
        storage: FillSetStorageInterface,
        steps: StepsManagerInterface,
        resolver: ModelResolverInterface,
    ) -> None:
        self._storage = storage
        self._steps = steps
        self._resolver = resolver

    async def resolve_fill_set(
        self,
        ctx: TenantContext,
        session_id: UUID,
        roles: Sequence[ModelRole],
        eligibility: Eligibility,
    ) -> FillSet:
        ctx.require(Permission.WRITE)
        head = await self._storage.read_fill_set(ctx.org_id, session_id, None)
        if head is not None:
            return head
        first = FillSet(
            id=new_id(),
            created_at=utcnow(),
            session_id=session_id,
            version=1,
            roles=await self._resolver.resolve(ctx, roles, eligibility),
            eligibility=eligibility,
        )
        try:
            await self._storage.write_fill_set(ctx.org_id, first)
        except PreconditionFailed:
            # A resolution that raced this one wrote the first version: the
            # session keeps that one.
            return await self._head(ctx, session_id)
        return first

    async def get_fill_set(self, ctx: TenantContext, session_id: UUID) -> FillSet:
        ctx.require(Permission.READ)
        return await self._head(ctx, session_id)

    async def switch_fill(
        self,
        ctx: TenantContext,
        session_id: UUID,
        epoch: int,
        loop_id: UUID,
        role: ModelRole,
        to: Fill,
        reason: SwitchReason,
    ) -> FillSet:
        ctx.require(Permission.WRITE)
        head = await self._head(ctx, session_id)
        refusal = rules.switch_refusal(head, role, to)
        if refusal is not None:
            raise ValidationFailed(refusal)
        self._resolver.check(to)
        step = Step(
            id=new_id(),
            created_at=utcnow(),
            session_id=session_id,
            loop_id=loop_id,
            type=StepType.SWITCHED,
            actor=Actor.ENGINE,
            origin=Origin.ENGINE,
            header=SwitchedHeader(fills=rules.fill_switch(head, role, to, reason)),
        )
        # The history first: a run that lost its claim is refused here, and
        # nothing changes. The version follows the step it announces.
        (stored,) = await self._steps.append_steps(ctx, session_id, epoch, [step])
        return await self._record(ctx, head, stored)

    async def fall_back(
        self,
        ctx: TenantContext,
        session_id: UUID,
        epoch: int,
        loop_id: UUID,
        role: ModelRole,
        tried: Collection[Fill] = (),
    ) -> FillSet | None:
        ctx.require(Permission.WRITE)
        head = await self._head(ctx, session_id)
        to = rules.next_fallback(head, role, tried)
        if to is None:
            return None
        return await self.switch_fill(
            ctx, session_id, epoch, loop_id, role, to, SwitchReason.FALLBACK
        )

    async def settle_switch(self, ctx: TenantContext, step: Step) -> FillSet:
        ctx.require(Permission.WRITE)
        if not isinstance(step.header, SwitchedHeader):
            raise ValidationFailed(f"step {step.id} is not a fill switch")
        version = step.header.fills.fill_set_version
        stored = await self._storage.read_fill_set(ctx.org_id, step.session_id, version)
        if stored is not None:
            if stored.switched_by != step.id:
                raise PreconditionFailed(f"version {version} is another switch's than {step.id}")
            return stored
        head = await self._head(ctx, step.session_id)
        if head.version != version - 1:
            raise PreconditionFailed(
                f"step {step.id} announces version {version}, and the head is {head.version}"
            )
        return await self._record(ctx, head, step)

    async def _head(self, ctx: TenantContext, session_id: UUID) -> FillSet:
        head = await self._storage.read_fill_set(ctx.org_id, session_id, None)
        if head is None:
            raise NotFound(f"session {session_id} has no fill set")
        return head

    async def _record(self, ctx: TenantContext, head: FillSet, step: Step) -> FillSet:
        """Writes the version `step` announces over `head`; written before, it
        is answered as stored."""
        if not isinstance(step.header, SwitchedHeader):
            raise ValidationFailed(f"step {step.id} is not a fill switch")
        version = rules.switched(head, step.header.fills, step.id, step.created_at)
        await self._storage.write_fill_set(ctx.org_id, version)
        return version
