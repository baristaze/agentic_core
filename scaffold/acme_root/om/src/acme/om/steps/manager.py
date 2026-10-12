"""The steps swimlane: the history of every session, append-only, the record
every other part of the engine appends to or reads. It is the source of
truth; a session's status, its context, its trace, and its cost are
queries or projections over it.

Two appends write it. A run's append names the writer epoch the run took
when it began, and is refused once another run has begun. The inbox's
append takes inputs and controls, which arrive whether or not a run holds
the session, and the switches a person makes to it, and nothing else.

Both write a person's message in the name of the context that appends
it: its principal is that context's user, never one the caller wrote, and
it is a participant's, data, unless that user steers the session
(`attribution.rules.said_by`, `SteerCheck`). A person's decision on a tool
call is written the same way, with the role that context holds
(`attribution.rules.decided_by`), and so is who switched the session's
steerer or who sees it (`attribution.rules.switched_by`).

A history is read by whom its session is visible to (`OpenCheck`)."""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from uuid import UUID

from acme.om.context import TenantContext
from acme.om.steps.types.page import StepCursor, StepPage
from acme.om.steps.types.step import Step

InstructCheck = Callable[[TenantContext, UUID], Awaitable[None]]
"""Refuses, with `NotAuthorized`, a context that may not instruct a session:
one that lacks a permission a call the session's registry offers needs. The
agents manager answers it, since a session's registry is its kind's; a root
binds the two."""

SteerCheck = Callable[[TenantContext, UUID], Awaitable[bool]]
"""Whether the context's person steers a session: True for its steerer,
False for a participant who does not, and `NotAuthorized` for anyone else,
who joins it first. Asked of a message a person or a program writes, and of
standing text, which only the steerer pins. Attribution answers it, since
the steerer is the session's principal; a root binds the two."""

OpenCheck = Callable[[TenantContext, UUID], Awaitable[None]]
"""Refuses, with `NotFound`, a context the session is not visible to, as
one that never existed. Not asked of a context that holds
`manage_sessions`. The agent sessions manager answers it; a root binds the
two."""


class StepsManagerInterface(ABC):
    @abstractmethod
    async def begin_run(self, ctx: TenantContext, session_id: UUID) -> int:
        """A new run's writer epoch, larger than any before it on this
        session. A run takes it before it reads the history, names it on
        every append, and is refused from the moment a later run takes its
        own."""
        ...

    @abstractmethod
    async def append_steps(
        self, ctx: TenantContext, session_id: UUID, epoch: int, steps: Sequence[Step]
    ) -> tuple[Step, ...]:
        """A run's append, conditional on `epoch`: the steps take the
        session's next seqs, in the order given, and come back with them.
        `StaleWriter` when the session is held at another epoch, with nothing
        written. A step appended before is answered as stored. A batch past
        the bound, one that names a step of another session, or one that
        names an id twice is `ValidationFailed`. A person's message is asked
        of `SteerCheck`, and an instruction of `InstructCheck`, as the inbox
        asks them."""
        ...

    @abstractmethod
    async def append_inputs(
        self, ctx: TenantContext, session_id: UUID, steps: Sequence[Step]
    ) -> tuple[Step, ...]:
        """The inbox's append: inputs, controls, and the switches a person
        makes to the session, durable when this returns, with no epoch. A
        step of any other type is `ValidationFailed`. A person's message is
        the steerer's, an instruction, or a participant's, data
        (`SteerCheck`); from anyone else, and standing text from anyone but
        the steerer, it is `NotAuthorized`. An instruction, the steerer's
        message or a parent's to its child, from a context that may not
        make every kind of call the session's registry offers is
        `NotAuthorized` (`InstructCheck`). A refusal appends nothing."""
        ...

    @abstractmethod
    async def get_steps(
        self, ctx: TenantContext, session_id: UUID, after_seq: int, limit: int
    ) -> StepPage:
        """One page of the session's history in `seq` order, strictly after
        `after_seq`; `limit` is clamped. A session not visible to the
        context is `NotFound` (`OpenCheck`)."""
        ...

    @abstractmethod
    async def get_cursor(self, ctx: TenantContext, session_id: UUID) -> StepCursor:
        """The session's head and the epoch of the run that holds it."""
        ...

    @abstractmethod
    async def purge_tenant(self, ctx: TenantContext) -> int:
        """The sweep, for one tenant past its own retention: its steps, a
        batch at most a call, then its cursor rows, under the purge login,
        since no serving login may delete a step (ADR 1002, ADR 1010). Any
        other tenant returns 0 and reads nothing."""
        ...

    @abstractmethod
    async def purge_histories(
        self, sessions: Sequence[tuple[UUID, UUID]]
    ) -> list[tuple[UUID, UUID]]:
        """Platform-internal: the sweep's, for no principal: for each
        session, named with its tenant, a batch of its steps at most, then
        its cursor row once none is left, under the purge login. It holds no retention
        of its own: only the agent sessions' purge calls it, with sessions it
        has claimed past theirs (`AgentSessionsManagerInterface.purge_across_tenants`).
        Returns the sessions whose history is gone."""
        ...
