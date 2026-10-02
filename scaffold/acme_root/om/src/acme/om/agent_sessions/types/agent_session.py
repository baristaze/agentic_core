"""A session: a series of loops over one history. It never ends: a loop
ends, and an input that wakes the session starts the next one.

The entity holds what no step does: who made it, its title, its
participants, its parent and its root. Its status is a projection of its
steps, cached here for queries; the steps are the truth, and the cache is
rebuilt from them (`agent_sessions.rules.projected`). In code it is
`AgentSession`, never the sign-in `Session` of the tenancy namespace."""

from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Self
from uuid import UUID

from pydantic import Field, model_validator

from acme.om.base import Identifiable, Platform, Trackable
from acme.om.steps.types.content import Stored
from acme.om.steps.types.header import Park


class SessionStatus(StrEnum):
    PENDING = "pending"  # an input that wakes it waits for a run
    RUNNING = "running"  # a run holds its loop
    PARKED = "parked"  # its loop waits on an unlock
    IDLE = "idle"  # no loop is open: none began, or the last one ended


class AgentSession(Identifiable, Trackable):
    MANAGER_OWNED_FIELDS: ClassVar[tuple[str, ...]] = (
        "root_id",
        "status",
        "park",
        "status_seq",
        "pending_input",
        "delivering_request",
        "archived_at",
        "version",
    )
    """The root follows the parent, and the rest is the projection's."""

    title: Stored = Field(min_length=1, max_length=200)
    participants: tuple[UUID, ...] = ()  # the users the session is shared with
    parent_id: UUID | None = None  # the session that spawned this one
    root_id: UUID  # its tree's root; its own id when it has no parent
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

    @model_validator(mode="after")
    def _a_park_is_the_parked_status(self) -> Self:
        if (self.park is not None) != (self.status is SessionStatus.PARKED):
            raise ValueError("a session carries a park exactly while it is parked")
        return self


class AgentSessionPage(Platform):
    items: tuple[AgentSession, ...]
    has_more: bool
