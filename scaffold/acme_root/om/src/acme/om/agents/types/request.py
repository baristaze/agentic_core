"""What a caller asks of the agents swimlane: a root session started on a
kind, a child spawned by a parent, and work handed from one kind to
another.

None of them names a principal, a spender, a mark, or a budget. A root's
principal is the person who starts it; a child's and a handed-over
session's come from the session they came from, and so do their mark and,
for a child, its spender and its tree. A spawn's or a hand-over's text is
self-contained: a child never reads its parent's history."""

from datetime import datetime
from uuid import UUID

from pydantic import Field

from acme.om.attribution.types.principal import MAX_KIND
from acme.om.base import Platform

MAX_TITLE = 200

MAX_OBJECTIVE = 8_000
"""The most a spawn's or a hand-over's objective holds, in characters: room
for the objective, its constraints and bounds, and the shape of a good
report. Large material crosses by its handle, never pasted, so the window
it opens stays small."""


class Start(Platform):
    """A root session on the latest version of a kind. `deadline` is the
    tree's instant; None takes the kind's from now."""

    id: UUID
    kind: str = Field(min_length=1, max_length=MAX_KIND)
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    participants: tuple[UUID, ...] = ()
    deadline: datetime | None = None


class Spawn(Platform):
    """A child of the session that asks. `id` is taken from the spawning
    tool call's request, so a retry finds the child it made. `objective`
    holds what the child starts from: the objective, the constraints that
    bind it, its bounds, and the shape of a good report. With `fork`, its
    workspace starts from a copy of its parent's latest snapshot."""

    id: UUID
    kind: str = Field(min_length=1, max_length=MAX_KIND)
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    objective: str = Field(min_length=1, max_length=MAX_OBJECTIVE)
    fork: bool = False


class Handoff(Platform):
    """Work handed from one kind to another: a new root session that holds
    `objective` and where it came from, and waits for its principal to
    confirm it."""

    id: UUID
    kind: str = Field(min_length=1, max_length=MAX_KIND)
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    objective: str = Field(min_length=1, max_length=MAX_OBJECTIVE)
