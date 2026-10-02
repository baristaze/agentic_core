from uuid import UUID

from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from acme.om.storage.tables.base import Base


class StepCursors(Base):
    """One row per session. `head` is the last seq the append assigned: an
    append takes `head + n` for its n new steps under this row's lock, in the
    statement that writes them, and holds the lock to its commit, so two
    appends to one session queue here, commit in the order of their numbers,
    and a rollback returns the numbers.

    `epoch` is the writer epoch of the run that holds the session: a run
    that begins moves it one up, and a run's append changes the row only
    while it still holds the epoch the run took, so a run that lost its claim
    writes nothing. 0 until the first run begins."""

    __tablename__ = "step_cursors"
    org_id: Mapped[UUID] = mapped_column(primary_key=True)
    session_id: Mapped[UUID] = mapped_column(primary_key=True)
    head: Mapped[int] = mapped_column(BigInteger)
    epoch: Mapped[int] = mapped_column(BigInteger)
