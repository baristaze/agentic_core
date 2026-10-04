from uuid import UUID

from sqlalchemy import BigInteger, Index
from sqlalchemy.orm import Mapped, mapped_column

from acme.om.storage.tables.base import Base, CreatedMixin, IdentifiableMixin


class UsageRecords(IdentifiableMixin, CreatedMixin, Base):
    """What each billed model call used and cost: one row per hold, written
    once, holding ids, counts, money, a duration, and labels, and no content.
    The serving logins hold SELECT and INSERT here and nothing more, and the
    purge login holds nothing: a session's purge and a tenant's leave it, as
    they leave the ledger (ADR 1014)."""

    __tablename__ = "usage_records"
    # org_id leads both indexes, so it gets none of its own.
    __org_id_index__ = False
    __table_args__ = (
        # A call is recorded once: a second record of its hold meets this index.
        Index("uq_usage_records_org_id_hold_id", "org_id", "hold_id", unique=True),
        # A session's records, in the order they were written, and its rollups.
        Index("ix_usage_records_org_id_session_id_id", "org_id", "session_id", "id"),
    )
    hold_id: Mapped[UUID]
    session_id: Mapped[UUID]
    loop_id: Mapped[UUID]
    step_id: Mapped[UUID]
    agent_kind: Mapped[str]
    role: Mapped[str]
    provider: Mapped[str]
    model: Mapped[str]
    input_tokens: Mapped[int] = mapped_column(BigInteger)
    cache_read_tokens: Mapped[int] = mapped_column(BigInteger)
    cache_write_tokens: Mapped[int] = mapped_column(BigInteger)
    output_tokens: Mapped[int] = mapped_column(BigInteger)
    thinking_tokens: Mapped[int] = mapped_column(BigInteger)
    cost_micros: Mapped[int | None] = mapped_column(BigInteger)
    latency_ms: Mapped[int] = mapped_column(BigInteger)
