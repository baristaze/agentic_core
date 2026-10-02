"""The pinned zone: what every request carries through any compaction. It is
built only from principal-authored messages, so nothing a tool returned, an
event said, or a summary folded in becomes an instruction."""

from uuid import UUID

from pydantic import Field

from acme.om.base import Platform


class PinnedItem(Platform):
    """One principal message in the zone: quoted whole, or, beyond the
    zone's bound, an excerpt that cites the message by its step."""

    step_id: UUID
    seq: int = Field(ge=1)
    text: str
    whole: bool


class PinnedZone(Platform):
    """The session's objective, its first principal message, and the
    standing instructions of the messages after it, as of the latest
    summary. Messages past the digest's bound are counted and cited by the
    range of the history that holds them."""

    objective: PinnedItem | None = None
    instructions: tuple[PinnedItem, ...] = ()
    omitted: int = Field(default=0, ge=0)
    omitted_from: int | None = None
    omitted_to: int | None = None

    def is_empty(self) -> bool:
        return self.objective is None
