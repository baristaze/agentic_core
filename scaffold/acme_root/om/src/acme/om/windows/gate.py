"""The budget gate as a compaction asks it. A compaction is a model call, and
it passes the one gate before it starts like any other: a hold of the
call's worst case first, settled once the provider answers. This is the
narrow face of the gate the windows read; a root wires the budgets' gate
behind it."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.integrations.model_providers.calls import ModelCall
from acme.integrations.model_providers.types import Usage
from acme.om.context import TenantContext
from acme.om.models.types.fill import Fill, ModelRole


class CallGateInterface(ABC):
    @abstractmethod
    async def authorize(
        self, ctx: TenantContext, session_id: UUID, role: ModelRole, fill: Fill, call: ModelCall
    ) -> UUID:
        """The id of a hold of the call's worst case. A refusal raises with
        nothing held and nothing spent, and the call is never made."""
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
