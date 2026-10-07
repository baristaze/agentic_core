"""The stream sink: where the loop emits the parts of what it is writing, as
they arrive, for a carrier to deliver to whoever watches.

Emission never blocks the loop. `emit` is not awaited and answers nothing:
a slow viewer is the carrier's problem, never the agent's, so a carrier
that cannot keep up drops or coalesces parts and never holds the loop
back. A part is never a step: the step it adds up to is stored once, whole,
when its stream ends.

The loop also says when a stream opens, before its first part, and when it
completes, once its step is stored or its call has failed, so a carrier can
tell whoever watches. Neither waits."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.context import TenantContext
from acme.om.steps.types.stream import StreamPart


class StreamSinkInterface(ABC):
    @abstractmethod
    def emit(self, part: StreamPart) -> None:
        """Hands a part to the carrier and returns at once."""
        ...

    @abstractmethod
    def opened(self, ctx: TenantContext, session_id: UUID, step_id: UUID) -> None:
        """The stream of `step_id` opens, under the run's context. Returns at
        once."""
        ...

    @abstractmethod
    def completed(self, ctx: TenantContext, session_id: UUID, step_id: UUID) -> None:
        """The stream of `step_id` is over: its step is stored, or its call
        failed. Returns at once."""
        ...
