"""A question to an agent kind whose loop runs in the request that asks it
(ADR 1044): the carrier that hands what the loop streams to that request,
and the stream the request answers with, as server-sent events.

The carrier is the process's stream sink. A request subscribes to the
session it asks for as long as it runs the session's loop, and each part
the loop emits for that session lands in its queue. A part no request
subscribes to goes nowhere, and an emit never waits.

The stream names the session first. Then it sends each text part the
model writes, with the id of the step it adds up to, a ping after each
spell of quiet, and one end event: how the run ended, the loop's outcome
and its cause, the seqs of the steps the question led to, and what its
model calls used. Each event's data is one line of JSON, so nothing the
model writes opens an event of its own. A caller that goes away cancels
the loop at its next await, and nothing of it outlives the request: the
session's next question takes the open loop up."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from acme.om.agent_steps.types.stream import StreamPart, TextPart
from acme.om.agents.sink import StreamSinkInterface
from acme.om.agents.types.run import LoopRun
from acme.om.context import TenantContext
from acme.om.exceptions import OmException
from acme.services.api.types.agent_sessions import (
    QuestionEndView,
    QuestionErrorView,
    QuestionPingView,
    QuestionSessionView,
    QuestionTextView,
)

log = logging.getLogger(__name__)

QUIET = timedelta(seconds=15)
"""The longest a stream stays silent: a ping follows each spell this long,
so no proxy between the caller and the process cuts a stream that waits on
a model or a tool."""

Sleep = Callable[[float], Awaitable[None]]
"""How the stream waits out a spell of quiet, injected so a test never
sleeps."""


class _Ran:
    """The mark the loop's task leaves in the queue once it is done."""


RAN = _Ran()


class QuestionStreams(StreamSinkInterface):
    """The stream sink of a process that runs loops in the requests that ask
    them. Each request subscribes to its session for as long as it runs the
    session's loop; a writer epoch lets one run write at a time, so a part
    reaches the request that runs the loop."""

    def __init__(self) -> None:
        self._queues: dict[UUID, list[asyncio.Queue[StreamPart | _Ran]]] = {}

    def emit(self, part: StreamPart) -> None:
        for queue in self._queues.get(part.agent_session_id, ()):
            queue.put_nowait(part)

    def opened(self, ctx: TenantContext, agent_session_id: UUID, agent_step_id: UUID) -> None:
        return None

    def completed(self, ctx: TenantContext, agent_session_id: UUID, agent_step_id: UUID) -> None:
        return None

    @contextmanager
    def subscribed(self, agent_session_id: UUID) -> Iterator[asyncio.Queue[StreamPart | _Ran]]:
        """A queue of every part the session's loop emits from now until the
        block ends."""
        queue: asyncio.Queue[StreamPart | _Ran] = asyncio.Queue()
        self._queues.setdefault(agent_session_id, []).append(queue)
        try:
            yield queue
        finally:
            queues = self._queues[agent_session_id]
            queues.remove(queue)
            if not queues:
                del self._queues[agent_session_id]


def event(name: str, data: BaseModel) -> str:
    """One server-sent event: its name, and its data as one line of JSON."""
    return f"event: {name}\ndata: {data.model_dump_json()}\n\n"


async def stream_question(
    streams: QuestionStreams,
    asked: QuestionSessionView,
    run: Callable[[], Coroutine[Any, Any, LoopRun]],
    ended: Callable[[LoopRun], Awaitable[QuestionEndView]],
    *,
    quiet: timedelta = QUIET,
    sleep: Sleep = asyncio.sleep,
) -> AsyncIterator[str]:
    """The events of one question: the session, each text part, a ping after
    each spell of `quiet`, and the end `ended` reads off the run, or an
    error when the run raised. The loop runs in this request's task tree
    and goes with it."""
    yield event("session", asked)
    with streams.subscribed(asked.agent_session_id) as parts:
        task = asyncio.create_task(run())
        task.add_done_callback(lambda _: parts.put_nowait(RAN))
        try:
            while True:
                part = await _next(parts, quiet, sleep)
                if part is None:
                    yield event("ping", QuestionPingView())
                elif isinstance(part, _Ran):
                    break
                elif isinstance(part, TextPart):
                    yield event(
                        "text",
                        QuestionTextView(
                            agent_step_id=part.agent_step_id,
                            n=part.n,
                            index=part.index,
                            text=part.text,
                        ),
                    )
        finally:
            # A caller that went away leaves the loop to stop at its next
            # await; one that ran to its end is cancelled to no effect.
            task.cancel()
    try:
        run_ended = task.result()
    except OmException as error:
        log.warning("question to session %s: %s", asked.agent_session_id, error.message)
        yield event("error", QuestionErrorView(code=error.code, message=error.message))
        return
    except Exception:
        log.exception("question to session %s: the run failed", asked.agent_session_id)
        yield event("error", QuestionErrorView(code="internal", message="the run failed"))
        return
    yield event("end", await ended(run_ended))


async def _next(
    parts: asyncio.Queue[StreamPart | _Ran], quiet: timedelta, sleep: Sleep
) -> StreamPart | _Ran | None:
    """The next part in the queue, or None once `quiet` passes with none."""
    getting = asyncio.ensure_future(parts.get())
    waiting = asyncio.ensure_future(sleep(quiet.total_seconds()))
    try:
        await asyncio.wait({getting, waiting}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        waiting.cancel()
        if not getting.done():
            getting.cancel()
    return getting.result() if getting.done() and not getting.cancelled() else None
