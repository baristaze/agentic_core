"""The models swimlane: model roles, fills, a session's fill set and its
versions, and the switches between them.

A session resolves its fills once and keeps them; switching models over one
prompt prefix discards the prompt cache, so the engine never picks a model
per call. A switch is explicit: the `switched` step that names both fills
lands in the history first, under the run's writer epoch, and the version
it announces follows. The history is the truth, so a version a crash left
unwritten is written from its step (`settle_switch`), and a run that lost
its claim writes neither."""

from abc import ABC, abstractmethod
from collections.abc import Collection, Sequence
from datetime import datetime
from uuid import UUID

from acme.om.context import TenantContext
from acme.om.models.types.fill import (
    Eligibility,
    Fill,
    FillSet,
    FillSwitch,
    ModelRole,
    SwitchReason,
)


class ModelsManagerInterface(ABC):
    @abstractmethod
    async def resolve_fill_set(
        self,
        ctx: TenantContext,
        session_id: UUID,
        roles: Sequence[ModelRole],
        eligibility: Eligibility,
    ) -> FillSet:
        """The session's fill set, resolved once: its first version, from the
        resolver, when it has none; its latest when it has one, whatever
        `roles` and `eligibility` say then. `UnresolvedRole` or
        `UnpricedModel` from the resolver, with nothing written."""
        ...

    @abstractmethod
    async def get_fill_set(self, ctx: TenantContext, session_id: UUID) -> FillSet:
        """The latest version of the session's fill set; `NotFound` before it
        was resolved, and for another tenant's session."""
        ...

    @abstractmethod
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
        """An explicit switch of `role` to `to`, by the run at `epoch` inside
        the loop `loop_id`: a `switched` step naming both fills, then the
        version it announces, which this returns. `StaleWriter` when the run
        lost its claim, with nothing written. A fill the session's
        eligibility does not admit, a role the set does not hold, or the fill
        it holds already is `ValidationFailed`; a model with no price row is
        `UnpricedModel`; both before anything is written."""
        ...

    @abstractmethod
    async def fall_back(
        self,
        ctx: TenantContext,
        session_id: UUID,
        epoch: int,
        loop_id: UUID,
        role: ModelRole,
        tried: Collection[Fill] = (),
    ) -> FillSet | None:
        """The switch of `role` to its next declared fallback that the
        session's eligibility admits and `tried` does not name, as
        `switch_fill` makes it; None, with nothing written, when none is
        left, and the caller parks."""
        ...

    @abstractmethod
    async def settle_switch(
        self,
        ctx: TenantContext,
        session_id: UUID,
        step_id: UUID,
        fills: FillSwitch,
        at: datetime,
    ) -> FillSet:
        """The version the `switched` step `step_id` of the session
        announced, as its header names it (`fills`) at the time it was
        written (`at`): written when a crash came between the step and it,
        the stored one when it is there. A step that announces a version
        past the next, or a version another switch holds, is
        `PreconditionFailed`. The manager names no step type, so a step's
        header can name a fill without importing it."""
        ...

    @abstractmethod
    async def purge_tenant(self, ctx: TenantContext) -> int:
        """The sweep, for one tenant past its own retention: every fill-set
        version, a batch at most a call. Any other tenant returns 0 and
        reads nothing."""
        ...
