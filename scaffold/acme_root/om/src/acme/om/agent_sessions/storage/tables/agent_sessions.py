from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, Index
from sqlalchemy.orm import Mapped, mapped_column

from acme.om.storage.tables.base import Base, IdentifiableMixin, TrackableMixin


class AgentSessions(IdentifiableMixin, TrackableMixin, Base):
    __tablename__ = "agent_sessions"
    # org_id leads every index of a tenant's sessions, so it gets none of
    # its own.
    __org_id_index__ = False
    __table_args__ = (
        # The tenant's sessions by id, and those in one status by id: the
        # status is cached for exactly this read.
        Index("ix_agent_sessions_org_id_id", "org_id", "id"),
        Index("ix_agent_sessions_org_id_status_id", "org_id", "status", "id"),
        # A parent's children by id: the cancel that cascades reads them.
        Index("ix_agent_sessions_org_id_parent_id_id", "org_id", "parent_id", "id"),
    )
    title: Mapped[str]
    participants: Mapped[list[str]]
    kind: Mapped[str]
    kind_version: Mapped[int]
    tools: Mapped[list[str]]
    parent_id: Mapped[UUID | None]
    root_id: Mapped[UUID]
    depth: Mapped[int]
    handed_off_from: Mapped[UUID | None]
    speaker: Mapped[dict[str, Any] | None]
    untrusted: Mapped[bool]
    status: Mapped[str]
    park: Mapped[dict[str, Any] | None]
    # The same width as a step's seq.
    status_seq: Mapped[int] = mapped_column(BigInteger)
    pending_input: Mapped[UUID | None]
    delivering_request: Mapped[UUID | None]
    archived_at: Mapped[datetime | None]
    version: Mapped[int]
