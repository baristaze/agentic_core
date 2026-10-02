from uuid import UUID

from acme.om.agents.gate import ResultGateInterface
from acme.om.agents.rules import OUTCOMES
from acme.om.agents.types.result import Result, Verdict
from acme.om.context import TenantContext


class ResultGateNullImpl(ResultGateInterface):
    """Quiet: accepts every result with the outcome it claims, and marks it
    unverified. It never says a result was checked."""

    async def check(self, ctx: TenantContext, session_id: UUID, result: Result) -> Verdict:
        return Verdict(accepted=True, verified=False, outcome=OUTCOMES[result.claim])
