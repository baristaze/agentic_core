from datetime import datetime
from uuid import UUID

from sqlalchemy import Index, LargeBinary
from sqlalchemy.orm import Mapped, mapped_column

from acme.om.storage.tables.base import Base, CreatedMixin, IdentifiableMixin


class SessionKeys(IdentifiableMixin, CreatedMixin, Base):
    """One row per version of a session's key: its wrapped copy until the
    key is revoked, and the row after. A rotation rewrites the wrapped copy
    and nothing a step holds."""

    __tablename__ = "session_keys"
    # A session's versions are read together, in order, and a tenant's by
    # when they were wrapped: org_id leads the one compound index, so it gets
    # none of its own.
    __org_id_index__ = False
    __table_args__ = (
        Index(
            "uq_session_keys_org_id_session_id_version",
            "org_id",
            "session_id",
            "version",
            unique=True,
        ),
    )
    session_id: Mapped[UUID]
    version: Mapped[int]
    wrapped: Mapped[bytes | None] = mapped_column(LargeBinary)
    wrapping: Mapped[str | None]
    wrapped_at: Mapped[datetime | None]
    destroyed_at: Mapped[datetime | None]
