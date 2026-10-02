from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Index
from sqlalchemy.orm import Mapped

from acme.om.storage.tables.base import Base, CreatedMixin, IdentifiableMixin


class SessionPrivacyRows(IdentifiableMixin, CreatedMixin, Base):
    """One row per session of a tenant: its storage policy, written once,
    and the revocation of its key."""

    __tablename__ = "session_privacy"
    # A record is found by its session, and a session is the tenant's:
    # org_id leads the one index, so it gets none of its own.
    __org_id_index__ = False
    __table_args__ = (
        Index("uq_session_privacy_org_id_session_id", "org_id", "session_id", unique=True),
    )
    session_id: Mapped[UUID]
    policy: Mapped[dict[str, Any]]
    revoked_at: Mapped[datetime | None]
    revoked_by: Mapped[UUID | None]
