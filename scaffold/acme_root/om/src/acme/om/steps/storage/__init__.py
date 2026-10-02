"""Storage of the steps swimlane: the history of every session, append-only,
and each session's cursor row. Every operation takes org_id first.

A step is written once. There is no update here, and the one delete is the
purge, which runs under the purge login, the one login that may delete a
step; the serving logins may read and insert a step and nothing more (ADR
1002, ADR 1010). `seq` is the one number storage assigns, from the
session's cursor row, in the statement that writes the steps, because only
the database can order commits. The same row holds the writer epoch, which
fences every append a run makes."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from uuid import UUID

from acme.om.steps.types.page import StepCursor
from acme.om.steps.types.step import Step


class StepStorageInterface(ABC):
    @abstractmethod
    async def begin_run(self, org_id: UUID, session_id: UUID) -> int:
        """A new run's writer epoch: one statement moves the session's cursor
        row to an epoch one above the one it holds, and returns it, making
        the row at epoch 1 when the session has none. Two runs that begin at
        once queue on the row and leave with different epochs, the later one
        higher, and from that moment every append under a lower one is
        refused. A run takes it before it reads the history."""
        ...

    @abstractmethod
    async def append_steps(
        self, org_id: UUID, session_id: UUID, epoch: int, steps: Sequence[Step]
    ) -> tuple[Step, ...]:
        """A run's append, in one transaction, conditional on `epoch`: when
        the session's cursor row holds it, takes as many next seqs as there
        are new steps, writes the steps with them in the order given, and
        returns every step in that order. When the row holds another epoch,
        or the session has none, it raises `StaleWriter` and writes nothing:
        the run lost its claim. The numbers of one call are contiguous, two
        appends to one session queue on the row and never share a seq or
        leave a gap, and an append that rolls back returns its numbers.
        Idempotent on the id: a step already appended to this session is
        returned as stored and takes no number. An id another tenant holds
        refuses the whole call (`TenantMismatch`), and so does one another
        session holds (`UniqueKeyTaken`). A batch that names an id twice, or
        a step of another session, is refused before anything is written
        (`ValidationFailed`)."""
        ...

    @abstractmethod
    async def append_inputs(
        self, org_id: UUID, session_id: UUID, steps: Sequence[Step]
    ) -> tuple[Step, ...]:
        """The inbox's append: inputs and controls, which arrive whether or
        not a run holds the session, so no epoch fences them. Otherwise it
        is `append_steps`: the same numbers from the same row, the same
        idempotence, the same refusals, and the row made when the session
        has none, at epoch 0. A step of any other type refuses the batch
        (`ValidationFailed`): only a run appends what the engine writes."""
        ...

    @abstractmethod
    async def read_steps(
        self, org_id: UUID, session_id: UUID, after_seq: int, limit: int
    ) -> list[Step]:
        """The session's steps with seq greater than `after_seq`, ascending,
        at most `limit` of them."""
        ...

    @abstractmethod
    async def read_cursor(self, org_id: UUID, session_id: UUID) -> StepCursor:
        """The session's cursor row: its head and its epoch, both 0 when the
        session has none."""
        ...

    @abstractmethod
    async def purge_history(self, org_id: UUID, session_id: UUID, limit: int) -> int:
        """Under the purge login: at most `limit` of the session's steps, and
        its cursor row once none is left; returns how many rows went. Fewer
        than `limit` says the history is gone."""
        ...

    @abstractmethod
    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        """Under the purge login: at most `limit` of the tenant's steps, and,
        once none is left, of its cursor rows; returns how many rows went.
        Fewer than `limit` says nothing of the tenant's history is left."""
        ...
