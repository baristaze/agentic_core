"""A route answered with a stream of server-sent events: a question to a
kind whose loop runs in the request that asks it (ADR 1044). The service
writes what the request asks before anything streams, so its refusals
answer in the one error envelope; then the events go out as they come,
never stored, cached, or held back by a proxy that buffers."""

from collections.abc import AsyncIterator, Awaitable
from typing import Annotated, Any

from fastapi import Depends
from fastapi.responses import StreamingResponse

EVENT_STREAM = "text/event-stream"

EVENT_STREAM_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": (
            "The question's stream, as server-sent events: `session` "
            "(QuestionSessionView), then `text` (QuestionTextView) and `ping` "
            "(QuestionPingView), and one `end` (QuestionEndView) or `error` "
            "(QuestionErrorView)."
        ),
        "content": {EVENT_STREAM: {}},
    }
}
"""What a streamed route documents: its one answer, and the events in it."""


class EventStream:
    async def answer(self, events: Awaitable[AsyncIterator[str]]) -> StreamingResponse:
        """The events the service's call answers with, once it has written
        what the request asks."""
        return StreamingResponse(
            await events,
            media_type=EVENT_STREAM,
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )


def event_stream() -> EventStream:
    return EventStream()


Events = Annotated[EventStream, Depends(event_stream)]
