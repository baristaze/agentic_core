"""What a caller asks of the agents swimlane: a root session started on a
kind, a child spawned by a parent, work handed from one kind to another,
and a switch of a session's standing text.

None of them names a principal, a spender, a mark, or a budget. A root's
principal is the person who starts it; a child's and a handed-over
session's come from the session they came from, and so do their mark and,
for a child, its spender and its tree. A spawn's or a hand-over's text is
self-contained: a child never reads its parent's history."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, Field

from acme.om.attribution.types.principal import MAX_KIND
from acme.om.base import OmBase

MAX_TITLE = 200

MAX_OBJECTIVE = 8_000
"""The most a spawn's or a hand-over's objective holds, in characters: room
for the objective, its constraints and bounds, and the shape of a good
report. Large material crosses by its handle, never pasted, so the window
it opens stays small."""


MAX_STANDING = 24_000
"""The most a session's standing text holds, in characters. It renders
whole on every request of the session, so it is room every window gives
up for it: a procedure the work needs now and then is invoked when it is
needed, never pinned."""


def _bounded(text: str) -> str:
    if len(text) > MAX_STANDING:
        raise ValueError(
            f"standing text of {len(text)} characters is past its bound of "
            f"{MAX_STANDING}: it renders whole on every request, and would crowd out the work"
        )
    return text


StandingText = Annotated[str, AfterValidator(_bounded)]
"""A session's standing text: the instructions a principal pins on it,
which render in the system layer of every request. Past its bound it is
refused when it is pinned, with the reason, and nothing is made."""


class Start(OmBase):
    """A root session on the latest version of a kind. `deadline` is the
    tree's instant; None takes the kind's from now. `standing` is the
    standing text the session pins at its start, empty for none."""

    id: UUID
    kind: str = Field(min_length=1, max_length=MAX_KIND)
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    participants: tuple[UUID, ...] = ()
    deadline: datetime | None = None
    standing: StandingText = ""


class Spawn(OmBase):
    """A child of the session that asks. `id` is taken from the spawning
    tool call's request, so a retry finds the child it made. `objective`
    holds what the child starts from: the objective, the constraints that
    bind it, its bounds, and the shape of a good report. `standing` is the
    standing text it pins, which its maker sends and the model never
    writes: the engine's spawn tool sends none."""

    id: UUID
    kind: str = Field(min_length=1, max_length=MAX_KIND)
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    objective: str = Field(min_length=1, max_length=MAX_OBJECTIVE)
    standing: StandingText = ""


class Handoff(OmBase):
    """Work handed from one kind to another: a new root session that holds
    `objective` and where it came from, and waits for its principal to
    confirm it. `standing` is the standing text it pins, as a spawn's is."""

    id: UUID
    kind: str = Field(min_length=1, max_length=MAX_KIND)
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    objective: str = Field(min_length=1, max_length=MAX_OBJECTIVE)
    standing: StandingText = ""


class SwitchStanding(OmBase):
    """A switch of a session's standing text to `standing`, empty for none.
    `id` is the switch's step, so a retry finds the one it recorded. The
    session's requests render it from its next loop on."""

    id: UUID
    standing: StandingText
