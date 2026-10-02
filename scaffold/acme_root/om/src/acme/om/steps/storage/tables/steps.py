from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, Index
from sqlalchemy.orm import Mapped, mapped_column

from acme.om.storage.tables.base import Base, CreatedMixin, IdentifiableMixin


class Steps(IdentifiableMixin, CreatedMixin, Base):
    """The history: one row per step, written once. The serving logins hold
    SELECT and INSERT here and nothing more, so no statement a process sends
    rewrites or removes a step (ADR 1002)."""

    __tablename__ = "steps"
    # A history is read by session in seq order: org_id leads the one
    # compound index, so it gets none of its own, and a read by id is served
    # by the primary key. The unique (org_id, session_id, seq) guards the
    # cursor row's invariant; the append takes its numbers from the cursor,
    # never from this index.
    __org_id_index__ = False
    __table_args__ = (
        Index("uq_steps_org_id_session_id_seq", "org_id", "session_id", "seq", unique=True),
    )
    session_id: Mapped[UUID]
    # The same number as the cursor row's head, and the same width.
    seq: Mapped[int] = mapped_column(BigInteger)
    loop_id: Mapped[UUID]
    type: Mapped[str]
    actor: Mapped[str]
    origin: Mapped[str]
    responds_to: Mapped[UUID | None]
    refs: Mapped[list[str]]
    header: Mapped[dict[str, Any]]
    content: Mapped[dict[str, Any]]
    children: Mapped[dict[str, Any]]
