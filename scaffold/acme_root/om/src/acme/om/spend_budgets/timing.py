"""How long a model call took, on its caller's clock: from its request sent
to the first part its provider streamed back, and to its end. What a usage
record says of a call's time comes from here (`CallSite`)."""

from collections.abc import AsyncIterator, Callable
from datetime import datetime
from uuid import UUID

from acme.integrations.model_providers.calls import StreamPart
from acme.om.spend_budgets.rules import elapsed_ms
from acme.om.spend_budgets.types.usage import CallSite


class CallTimer:
    """One model call's clock. It starts when the call is sent, after the
    gate's hold and the request's step, so neither counts. `parts` passes
    the provider's stream through as it arrives and reads the clock at its
    first part, whatever its kind."""

    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self.started = clock()
        self.first: datetime | None = None

    async def parts(self, stream: AsyncIterator[StreamPart]) -> AsyncIterator[StreamPart]:
        async for part in stream:
            if self.first is None:
                self.first = self._clock()
            yield part

    def site(self, loop_id: UUID, agent_step_id: UUID, ended: datetime | None = None) -> CallSite:
        """Where the call sat and its times: to its first part, None when no
        part came, and to `ended`, now when not given."""
        end = self._clock() if ended is None else ended
        first = None if self.first is None else elapsed_ms(self.started, self.first)
        return CallSite(
            loop_id=loop_id,
            agent_step_id=agent_step_id,
            latency_ms=elapsed_ms(self.started, end),
            first_token_ms=first,
        )
