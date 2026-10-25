from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, Index, LargeBinary, text
from sqlalchemy.orm import Mapped, mapped_column

from acme.om.storage.tables.base import Base, IdentifiableMixin, SoftDeletableMixin, TrackableMixin

SEALED_TITLE = "(sealed)"
"""What `title` holds once the title is sealed or absent: a word, never the
title, for a release that reads that column alone."""


class AgentSessions(IdentifiableMixin, TrackableMixin, SoftDeletableMixin, Base):
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
        # A tree's sessions: a purge asks whether any is left besides its own.
        Index("ix_agent_sessions_org_id_root_id", "org_id", "root_id"),
        # The sweep's read across tenants: the deleted sessions by their
        # delete, the one column their retention counts from.
        Index(
            "ix_agent_sessions_deleted_at",
            "deleted_at",
            postgresql_where=text("deleted_at IS NOT NULL"),
        ),
        # The sweep's read across tenants: the titles still in the clear,
        # from before titles were sealed. Once each is sealed it holds none.
        Index(
            "ix_agent_sessions_title_clear",
            "id",
            postgresql_where=text("title_sealed IS NULL"),
        ),
    )
    # A title stored before titles were sealed, in the clear, until the sweep
    # seals it. Every other row holds `SEALED_TITLE`, so a release that reads
    # this column alone reads no title and fails no read.
    title: Mapped[str]
    # The title sealed under the session's key; empty once it is absent, and
    # NULL while `title` still holds it in the clear.
    title_sealed: Mapped[bytes | None] = mapped_column(LargeBinary)
    participants: Mapped[list[str]]
    visibility: Mapped[str]
    steerer: Mapped[UUID | None]
    kind: Mapped[str]
    kind_version: Mapped[int]
    tools: Mapped[list[str]]
    parent_id: Mapped[UUID | None]
    root_id: Mapped[UUID]
    depth: Mapped[int]
    handed_off_from: Mapped[UUID | None]
    speaker: Mapped[dict[str, Any] | None]
    untrusted: Mapped[bool]
    holds_private: Mapped[bool]
    status: Mapped[str]
    park: Mapped[dict[str, Any] | None]
    # The same width as a step's seq.
    status_seq: Mapped[int] = mapped_column(BigInteger)
    pending_input: Mapped[UUID | None]
    delivering_request: Mapped[UUID | None]
    archived_at: Mapped[datetime | None]
    version: Mapped[int]
    purge_started_at: Mapped[datetime | None]
