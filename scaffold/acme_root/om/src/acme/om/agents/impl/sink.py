from acme.infra.base import QuietNull
from acme.om.agents.sink import StreamSinkInterface
from acme.om.steps.types.stream import StreamPart


class StreamSinkNullImpl(StreamSinkInterface, QuietNull):
    """The sink of a root that wired no carrier. It is quiet: what is lost
    is the live view, and the steps still hold everything that was said."""

    def emit(self, part: StreamPart) -> None:
        return None


class StreamSinkMemoryImpl(StreamSinkInterface):
    """Keeps every part in this process, in the order emitted: a test's
    carrier, and a single process's."""

    def __init__(self) -> None:
        self.parts: list[StreamPart] = []

    def emit(self, part: StreamPart) -> None:
        self.parts.append(part)
