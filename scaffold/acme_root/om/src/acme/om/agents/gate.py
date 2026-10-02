"""The result gate: an injected check a result passes before it ends a
loop. The platform supplies the one that knows what evidence is; a claim
it cannot back is refused, and the model reads why.

The engine's own is the null gate (`impl/gate.py`): it accepts and marks
the result unverified, so nothing downstream mistakes it for checked."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.agents.types.result import Result, Verdict
from acme.om.context import TenantContext


class ResultGateInterface(ABC):
    @abstractmethod
    async def check(self, ctx: TenantContext, session_id: UUID, result: Result) -> Verdict:
        """Accepts a result, with the outcome it claims, or refuses it with
        the reason the model reads. Only a gate that judged the evidence
        answers `verified`."""
        ...
