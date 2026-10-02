from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Mapped

from acme.om.storage.tables.base import Base, CreatedMixin, IdentifiableMixin


class SessionPrivacyRows(IdentifiableMixin, CreatedMixin, Base):
    """One row per session, under the session's id: its storage policy,
    written once, and the revocation of its key."""

    __tablename__ = "session_privacy"
    policy: Mapped[dict[str, Any]]
    revoked_at: Mapped[datetime | None]
    revoked_by: Mapped[UUID | None]
