from typing import Any
from uuid import UUID

from sqlalchemy import Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from acme.om.storage.tables.base import Base, CreatedMixin, IdentifiableMixin


class FillSets(IdentifiableMixin, CreatedMixin, Base):
    """Every version of every session's fill set, one row each, written
    once: the storage interface has no update, so a fill changes only by a
    new version. A deleted tenant's rows go in the sweep's batches."""

    __tablename__ = "fill_sets"
    # A session's versions are read by number, the latest first: org_id
    # leads the one compound index, which also holds a version to one row.
    __org_id_index__ = False
    __table_args__ = (
        Index(
            "uq_fill_sets_org_id_session_id_version", "org_id", "session_id", "version", unique=True
        ),
    )
    session_id: Mapped[UUID]
    version: Mapped[int]
    roles: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    eligibility: Mapped[dict[str, Any]]
    reason: Mapped[str | None]
    switched_by: Mapped[UUID | None]
