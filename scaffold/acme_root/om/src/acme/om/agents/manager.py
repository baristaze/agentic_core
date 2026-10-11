"""The agents swimlane: agent kinds, the trees sessions form, and the work
one kind hands another. One loop serves every kind; a kind is a profile
over it, and a sub-agent is a session with a parent, spawned through a
`spawn`-class tool so it is gated and audited like any other power.

A child starts from a self-contained objective, never its parent's
history. It holds no more than its parent: its tools are cut to its
parent's, its calls run under its parent's principal, it pays as its
parent pays, it carries its parent's mark, and it draws on its tree's
budget and deadline, under a share of its own that never adds to the
tree's. Cancelling a parent cancels its children. A child's report
reaches its parent's inbox, as data, when its loop ends or when it needs
a person, so its parent never polls: it keeps working, or parks on its
children until a report wakes it."""

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from acme.om.agent_sessions.types.agent_session import AgentSession
from acme.om.agents.types.report import Report
from acme.om.agents.types.request import Handoff, Spawn, Start
from acme.om.agents.types.result import Result, Verdict
from acme.om.agents.types.tree import AgentTree
from acme.om.context import TenantContext
from acme.om.steps.types.step import Step
from acme.om.tools.tool import TakeSnapshot


class AgentsManagerInterface(ABC):
    @abstractmethod
    async def start_session(self, ctx: TenantContext, start: Start) -> AgentSession:
        """A root session on the latest version of its kind, run under the
        person who starts it, with its tree: the kind's bounds and one
        deadline. An unknown kind is `UnknownAgentKind`, and a person who
        lacks a permission a call its registry offers needs is
        `NotAuthorized`, with nothing made. An id written already answers
        the session as stored."""
        ...

    @abstractmethod
    async def require_instructor(self, ctx: TenantContext, session_id: UUID) -> None:
        """The history's check of an instruction, a principal's message or a
        parent's to its child: `NotAuthorized` when `ctx` lacks a permission
        a call the session's registry offers needs
        (`tools.rules.instruct_refusal`), or when the registry names a tool
        the catalog cannot class. A session that is not there offers nothing
        to check."""
        ...

    @abstractmethod
    async def spawn(
        self, ctx: TenantContext, parent_id: UUID, spawn: Spawn, fork: TakeSnapshot | None = None
    ) -> AgentSession:
        """A child of `parent_id`, one level down its tree, under a budget on
        its own session of its kind's share, and its objective as its first
        input: a waking message from its parent, through the inbox, so the
        projection that turns the child pending asks for its run. A tree past its height or
        its count is `TreeBoundReached`, a kind whose result tool its parent
        lacks or that names no share is `ValidationFailed`, and a context
        that lacks a permission a call of the child's registry needs is
        `NotAuthorized`: nothing is made.

        With `fork`, the spawning call's way to take its workspace as it
        stands (`ToolRuntime.snapshot`), the child's workspace starts from
        its own copy of it. The snapshot is taken before the tree's slot or
        the child, so one refused (`SnapshotRefused`) spends nothing.
        A spawn asked again under the same id answers the child it made, on
        the copy it was made with."""
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
        deadline parked. A deadline moved past now, or taken away, is that
        unlock: every session of the tree parked on the deadline is
        unlocked, and its gates run again when it resumes."""
        ...

    @abstractmethod
    async def cancel_children(self, ctx: TenantContext, session_id: UUID) -> tuple[UUID, ...]:
        """The cascade of a cancel: a `cancel` control to every session below
        `session_id` that is not idle, children and theirs, a session that
        waits to begin its next loop among them. Each goes through the inbox,
        so a parked child the cancel clears is asked to run and end its
        loop. Answers the sessions it reached."""
        ...

    @abstractmethod
    async def report_to_parent(
        self, ctx: TenantContext, session_id: UUID, report: Report
    ) -> Step | None:
        """A child's report, written into its parent's inbox as an agent's
        message: data that names the child and says how its loop stands and
        what it said last, carrying the child's mark and whether it holds
        private data, and above the size bound a tool result has, its head,
        its tail, and the handle of the artifact that keeps it whole. It
        wakes the parent through the inbox, so the projection that turns the
        parent pending asks for its run, except the note of a cancel that
        came down from the parent (`rules.report_wakes`); one that wakes a
        parent parked on its children clears that park
        (`rules.CHILDREN_PARK`).
        The report of a loop's end has an id derived from the loop, so a
        run that ends the loop again writes it once. None, with nothing
        written, for a session with no parent, a parent that is gone, and
        a park the parent is not told of (`rules.notes_parent`)."""
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
    async def purge_tree(self, org_id: UUID, tree_id: UUID) -> bool:
        """Platform-internal: a tree goes when the last of its sessions is
        purged, before that session's row, under the purge login, in the
        tenant named; for no principal. False when none was left."""
        ...

    @abstractmethod
    async def purge_tenant(self, ctx: TenantContext) -> int:
        """The sweep, for one tenant past its own retention: every tree, a
        batch at most a call. Any other tenant returns 0 and reads
        nothing."""
        ...
