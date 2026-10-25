from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Index, LargeBinary
from sqlalchemy.orm import Mapped, mapped_column

from acme.om.storage.tables.base import Base, CreatedMixin, IdentifiableMixin


class AgentSessionPrivacyRows(IdentifiableMixin, CreatedMixin, Base):
    """One row per session of a tenant: its storage policy, chosen once, its
    title key, and the revocation of its key."""

    __tablename__ = "agent_session_privacy"
    # A record is found by its session, and a session is the tenant's:
    # org_id leads the one index, so it gets none of its own.
    __org_id_index__ = False
    __table_args__ = (
        Index(
            "uq_agent_session_privacy_org_id_agent_session_id",
            "org_id",
            "agent_session_id",
            unique=True,
        ),
    )
    agent_session_id: Mapped[UUID]
    policy: Mapped[dict[str, Any]]
    chosen: Mapped[bool]
    # Wrapped under the tenant's title key; NULL from the revocation on.
    title_key: Mapped[bytes | None] = mapped_column(LargeBinary)
    revoked_at: Mapped[datetime | None]
    revoked_by: Mapped[UUID | None]
