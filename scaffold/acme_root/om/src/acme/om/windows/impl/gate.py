from uuid import UUID

from acme.integrations.model_providers.calls import ModelCall
from acme.integrations.model_providers.types import Usage
from acme.om.context import TenantContext
from acme.om.exceptions import Unavailable
from acme.om.models.types.fill import Fill, ModelRole
from acme.om.windows.gate import CallGateInterface


class CallGateNullImpl(CallGateInterface):
    """The gate of a root that wired none. It is loud: a compaction spends,
    and a call that skipped the gate would spend outside every budget, so it
    refuses and says why."""

    async def authorize(
        self, ctx: TenantContext, session_id: UUID, role: ModelRole, fill: Fill, call: ModelCall
    ) -> UUID:
        raise Unavailable("no budget gate is wired, so no compaction is called")

    async def settle(
        self, ctx: TenantContext, hold_id: UUID, usage: Usage | None, *, billed: bool
    ) -> None:
        raise Unavailable("no budget gate is wired, so there is no hold to settle")
