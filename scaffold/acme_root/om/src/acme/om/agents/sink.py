"""The stream sink: where the loop emits the parts of what it is writing, as
they arrive, for a carrier to deliver to whoever watches.

Emission never blocks the loop. `emit` is not awaited and answers nothing:
a slow viewer is the carrier's problem, never the agent's, so a carrier
that cannot keep up drops or coalesces parts and never holds the loop
back. A part is never a step: the step it adds up to is stored once, whole,
when its stream ends."""

from abc import ABC, abstractmethod

from acme.om.steps.types.stream import StreamPart


class StreamSinkInterface(ABC):
    @abstractmethod
    def emit(self, part: StreamPart) -> None:
        """Hands a part to the carrier and returns at once."""
        ...
