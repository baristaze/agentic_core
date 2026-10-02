"""The loop of a session, in the agents swimlane: the engine itself. The model
chooses; the engine does. One loop serves every agent kind: it renders a
request, passes the budget gate, persists the request, calls the model,
persists its response, and runs each tool call it asks for through policy
and the transport, persisting each call before it decides it and its
response before the next model call. It ends on the kind's done rule, a
bound, a principal's cancel, or an error no park can clear; it parks when
it cannot go on yet.

A loop is a span of steps and has no table: its id is its first step's,
and a `loop_ended` step closes it. Each run takes a writer epoch first, so
a run that lost its claim can write no step, and a run that finds a lost
run's calls unanswered settles each one by its effect."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.agent_sessions.types.agent_session import AgentSession
from acme.om.agents.types.run import LoopRun
from acme.om.context import TenantContext


class LoopManagerInterface(ABC):
    @abstractmethod
    async def run(self, ctx: TenantContext, session_id: UUID) -> LoopRun:
        """One run of the session's loop, under a writer epoch it takes
        first, above every one before. A session with nothing to run (idle,
        or parked on an unlock that has not happened) is answered `idle`
        with nothing written. Otherwise the run resumes a loop whose unlock
        happened, or begins the loop its waking input asks for; it prepares
        the session's workspace before its first model call, and a workspace
        that cannot meet the kind's isolation spec ends the loop `errored`
        before any call is made. It settles a lost run's open requests by
        their effect, then drives the loop until it ends, parks, or yields
        its run time. `stale` when a later run took the claim: nothing more
        is written, and the transport refuses this run's commands."""
        ...

    @abstractmethod
    async def take_over(self, ctx: TenantContext, session_id: UUID) -> AgentSession:
        """The caller takes the session's environment to work by hand: a new
        epoch fences the run that holds the loop, and the loop parks on a
        hand-over that only the giving back clears."""
        ...

    @abstractmethod
    async def give_back(self, ctx: TenantContext, session_id: UUID, summary: str) -> AgentSession:
        """The caller gives the environment back: an `environment_changed`
        step tells the model a person acted there, the summary arrives as
        the caller's message, and the hand-over park is unlocked, so the
        next run continues the loop and its first request delivers both. A
        session not parked on a hand-over is `ValidationFailed`."""
        ...
