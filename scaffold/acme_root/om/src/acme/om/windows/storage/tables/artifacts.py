from uuid import UUID

from sqlalchemy import BigInteger, Index
from sqlalchemy.orm import Mapped, mapped_column

from acme.om.storage.tables.base import Base, CreatedMixin, IdentifiableMixin


class Artifacts(IdentifiableMixin, CreatedMixin, Base):
    """The record of every artifact, one row each, written once beside the
    history it belongs to: the serving logins hold SELECT and INSERT here
    and nothing more, and the purge login, which takes it with its history,
    SELECT and DELETE (ADR 1010). The text is in the object store; a row is
    read by its id, which the primary key serves."""

    __tablename__ = "artifacts"
    # A purge reads a session's records, or a tenant's: org_id leads the one
    # compound index, so it gets none of its own.
    __org_id_index__ = False
    __table_args__ = (Index("ix_artifacts_org_id_session_id", "org_id", "session_id"),)
    session_id: Mapped[UUID]
    step_id: Mapped[UUID]
    # A step's sizes are the same width as its seq.
    characters: Mapped[int] = mapped_column(BigInteger)
