"""The budget gate as a compaction asks it, and as the loop asks it for a
model call or a job that spends. A compaction is a model call, and it
passes the one gate before it starts like any other: a hold of the call's
worst case first, settled once the provider answers. A job's worst case is
its rate until its deadline, settled once it ends. This is the narrow face
of the gate the windows and the loop read; a root wires the budgets' gate
behind it."""

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from acme.integrations.model_providers.calls import ModelCall
from acme.integrations.model_providers.types import Usage
from acme.om.attribution.types.principal import Principal
from acme.om.context import TenantContext
from acme.om.models.types.fill import Fill, ModelRole


class CallGateInterface(ABC):
    @abstractmethod
    async def authorize(
        self,
        ctx: TenantContext,
        session_id: UUID,
        spender: Principal,
        role: ModelRole,
        fill: Fill,
        call: ModelCall,
    ) -> UUID:
        """The id of a hold of the call's worst case on the budgets of
        `spender`, who pays for it. A refusal raises with nothing held and
        nothing spent, and the call is never made."""
        ...

    @abstractmethod
    async def settle(
        self, ctx: TenantContext, hold_id: UUID, usage: Usage | None, *, billed: bool
    ) -> None:
        """Closes the hold once: released when `billed` is False, which the
        caller says only when the call failed before the provider streamed
        anything back: it was never sent, or the provider refused it before
        processing it. Otherwise counted at `usage`, or at the whole hold when
        the usage is unknown."""
        ...

    @abstractmethod
    async def authorize_job(
        self,
        ctx: TenantContext,
        session_id: UUID,
        spender: Principal,
        tool: str,
        rate_micros_per_hour: int,
        deadline: datetime,
    ) -> UUID:
        """The id of a hold of a spending job's worst case, its rate until its
        deadline, on the budgets of `spender`, who pays for it. A refusal
        raises with nothing held, and the job is never started."""
        ...

    @abstractmethod
    async def settle_job(
        self, ctx: TenantContext, hold_id: UUID, cost_micros: int | None, *, started: bool
    ) -> None:
        """Closes a job's hold once: released when `started` is False, which
        the caller says only when the start was refused before any work
        began (`JobNotStarted`); otherwise counted at the cost its runner
        reported, or at the whole hold when none is reported."""
        ...
