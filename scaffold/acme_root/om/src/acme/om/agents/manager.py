"""The agents swimlane: agent kinds, the trees sessions form, and the work
one kind hands another. One loop serves every kind; a kind is a profile
over it, and a sub-agent is a session with a parent, spawned through a
`spawn`-class tool so it is gated and audited like any other power.

A child starts from a self-contained objective, never its parent's
history. It holds no more than its parent: its tools are cut to its
parent's, its calls run under its parent's principal, it pays as its
parent pays, it carries its parent's mark, and it draws on its tree's
budget and deadline. Cancelling a parent cancels its children."""

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from acme.om.agent_sessions.types.agent_session import AgentSession
from acme.om.agents.types.request import Handoff, Spawn, Start
from acme.om.agents.types.result import Result, Verdict
from acme.om.agents.types.tree import AgentTree
from acme.om.context import TenantContext


class AgentsManagerInterface(ABC):
    @abstractmethod
    async def start_session(self, ctx: TenantContext, start: Start) -> AgentSession:
        """A root session on the latest version of its kind, run under the
        person who starts it, with its tree: the kind's bounds and one
        deadline. An unknown kind is `UnknownAgentKind`. An id written
        already answers the session as stored."""
        ...

    @abstractmethod
    async def spawn(self, ctx: TenantContext, parent_id: UUID, spawn: Spawn) -> AgentSession:
        """A child of `parent_id`, one level down its tree, and its objective
        as its first input: a waking message from its parent. A tree past
        its height or its count is `TreeBoundReached`, and a kind whose
        result tool its parent lacks is `ValidationFailed`: nothing is made.
        A spawn asked again under the same id answers the child it made."""
        ...

    @abstractmethod
    async def tree_of(self, ctx: TenantContext, session_id: UUID) -> AgentTree:
        """The tree a session draws on: its root's record, whose id keys the
        budget every call of the tree is gated against, and whose deadline
        is the session's."""
        ...

    @abstractmethod
    async def set_deadline(
        self, ctx: TenantContext, session_id: UUID, deadline: datetime | None
    ) -> AgentTree:
        """Moves the deadline of the tree a session draws on, for every
        session of the tree at once, as a person does to unlock a loop the
        deadline parked."""
        ...

    @abstractmethod
    async def cancel_children(self, ctx: TenantContext, session_id: UUID) -> tuple[UUID, ...]:
        """The cascade of a cancel: a `cancel` control to every session below
        `session_id` that is not idle, children and theirs, a session that
        waits to begin its next loop among them. Answers the sessions it
        reached."""
        ...

    @abstractmethod
    async def hand_off(
        self, ctx: TenantContext, session_id: UUID, handoff: Handoff
    ) -> AgentSession:
        """Work handed from `session_id`'s kind to another: a root session of
        its own tree that names where it came from, runs under the principal
        `session_id`'s calls run under, and carries its mark. Its objective
        arrives as data and wakes nothing; it starts when its principal
        speaks, and the agent that handed it over has no way to steer it."""
        ...

    @abstractmethod
    async def judge_result(self, ctx: TenantContext, session_id: UUID, result: Result) -> Verdict:
        """The result gate. A claim that cites no evidence is refused before
        any gate looks; the rest is the injected gate's to accept or
        refuse."""
        ...

    @abstractmethod
    async def purge_tenant(self, ctx: TenantContext) -> int:
        """The sweep, for one tenant past its own retention: every tree, a
        batch at most a call. Any other tenant returns 0 and reads
        nothing."""
        ...
