"""The carrier of a question's stream: the stream sink of a process that
runs loops in the requests that ask them, which each such request
subscribes to for as long as it runs its session's loop (ADR 1044)."""

import asyncio
from abc import abstractmethod
from contextlib import AbstractContextManager
from uuid import UUID

from acme.om.agent_steps.types.stream import StreamPart
from acme.om.agents.sink import StreamSinkInterface


class QuestionStreamsInterface(StreamSinkInterface):
    @abstractmethod
    def subscribed(
        self, agent_session_id: UUID
    ) -> AbstractContextManager[asyncio.Queue[StreamPart | None]]:
        """A queue of every part the session's loop emits from now until the
        block ends. The request that runs the loop puts None in it once the
        run is done."""
        ...
