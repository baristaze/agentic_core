from uuid import UUID

from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from acme.om.storage.tables.base import Base, CreatedMixin, IdentifiableMixin


class Artifacts(IdentifiableMixin, CreatedMixin, Base):
    """The record of every artifact, one row each, written once beside the
    history it belongs to: the serving logins hold SELECT and INSERT here
    and nothing more. The text is in the object store; a row is read by its
    id, which the primary key serves."""

    __tablename__ = "artifacts"
    session_id: Mapped[UUID]
    step_id: Mapped[UUID]
    # A step's sizes are the same width as its seq.
    characters: Mapped[int] = mapped_column(BigInteger)
