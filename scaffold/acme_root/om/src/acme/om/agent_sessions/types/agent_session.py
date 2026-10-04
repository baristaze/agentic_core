"""A session: a series of loops over one history. It never ends: a loop
ends, and an input that wakes the session starts the next one.

The entity holds what no step does: who made it, its title, its
participants, its agent kind and version and the tools it may call, its
parent and its root, and the session that handed it over.
Its status is a projection of its steps, cached here for queries; the
steps are the truth, and the cache is rebuilt from them
(`agent_sessions.rules.projected`). The cache also holds two answers of
attribution: the speaker its latest model request recorded, and the
untrusted mark. It also holds whether it holds private data, which it
takes from where it came as it takes the mark. In code it is
`AgentSession`, never the sign-in `Session` of the tenancy namespace."""

from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Self
from uuid import UUID

from pydantic import Field, model_validator

from acme.om.attribution.types.principal import MAX_KIND, Principal
from acme.om.base import Identifiable, Platform, SoftDeletable, Trackable
from acme.om.steps.types.content import Stored
from acme.om.steps.types.header import Park


class SessionStatus(StrEnum):
    PENDING = "pending"  # an input that wakes it waits for a run
    RUNNING = "running"  # a run holds its loop
    PARKED = "parked"  # its loop waits on an unlock
    IDLE = "idle"  # no loop is open: none began, or the last one ended


class AgentSession(Identifiable, Trackable, SoftDeletable):
    MANAGER_OWNED_FIELDS: ClassVar[tuple[str, ...]] = (
        "root_id",
        "status",
        "park",
        "status_seq",
        "pending_input",
        "delivering_request",
        "archived_at",
        "version",
        "depth",
        "speaker",
        "untrusted",
        "purge_started_at",
    )
    """The root and the depth follow where the session came from, the purge
    claim is the sweep's, and the rest is the projection's; a session also
    takes its mark from where it came and, for a child, the cut of its tools
    (`agent_sessions.rules.lineage`)."""

    title: Stored = Field(min_length=1, max_length=200)
    participants: tuple[UUID, ...] = ()  # the users the session is shared with
    kind: str = Field(min_length=1, max_length=MAX_KIND)  # its agent kind, pinned
    kind_version: int = Field(ge=1)
    # Its registry: the tools it may call, by name, its kind's, cut to its
    # parent's for a child.
    tools: tuple[str, ...] = ()
    parent_id: UUID | None = None  # the session that spawned this one
    root_id: UUID  # its tree's root; its own id when it has no parent
    depth: int = Field(default=1, ge=1)  # 1 for a root; a child is its parent's plus one
    handed_off_from: UUID | None = None  # the session whose agent handed it the work
    # The speaker the latest model request the cache has read recorded.
    speaker: Principal | None = None
    # The mark: set by the first data, passed from where it came, never
    # cleared.
    untrusted: bool = False
    # Whether it holds private data or credentials, one of the rule of
    # two's three: set by its maker from its kind and its tools, passed from
    # where it came, never cleared. A child of a session that holds private
    # data may carry it in its objective, so it holds it too.
    holds_private: bool = False
    status: SessionStatus = SessionStatus.IDLE
    park: Park | None = None  # what a parked loop waits on
    # The last seq the cached status has read: the projection goes on from
    # the step after it.
    status_seq: int = Field(default=0, ge=0)
    # The last waking input no complete model response has delivered yet,
    # and the model request that carries it, once one does: a loop that ends
    # with an input still undelivered leaves the session pending.
    pending_input: UUID | None = None
    delivering_request: UUID | None = None
    # A flag, undone by a principal's message. An archived session records
    # what arrives and wakes for nothing else.
    archived_at: datetime | None = None
    # Every write after the create is a compare-and-set on it.
    version: int = Field(default=1, ge=1)
    # A session marked deleted (`deleted_at`) is hidden from every read and
    # comes back when unmarked. Past its retention the sweep claims it for
    # its purge, here, and from then on it cannot be unmarked: its history
    # goes, and then its row (ADR 1010).
    purge_started_at: datetime | None = None

    @model_validator(mode="after")
    def _a_park_is_the_parked_status(self) -> Self:
        if (self.park is not None) != (self.status is SessionStatus.PARKED):
            raise ValueError("a session carries a park exactly while it is parked")
        return self

    @model_validator(mode="after")
    def _one_lineage(self) -> Self:
        if self.parent_id is not None and self.handed_off_from is not None:
            raise ValueError("a session is spawned or handed over, not both")
        return self

    @model_validator(mode="after")
    def _a_purge_claims_a_deleted_session(self) -> Self:
        if self.purge_started_at is not None and self.deleted_at is None:
            raise ValueError("only a session marked deleted is claimed for its purge")
        return self


class AgentSessionPage(Platform):
    items: tuple[AgentSession, ...]
    has_more: bool
