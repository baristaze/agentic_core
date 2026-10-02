from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from acme.om.storage.tables.base import Base


class BudgetTallies(Base):
    """One row per budget and window: what open holds reserve there and what
    settlements spent. A hold and a settlement move it under its lock, in the
    transaction that writes them, and lock a call's rows in one order (by
    budget, then window), so two holds over one line queue here and never
    wait on each other crosswise (ADR 1006)."""

    __tablename__ = "budget_tallies"
    org_id: Mapped[UUID] = mapped_column(primary_key=True)
    budget_id: Mapped[UUID] = mapped_column(primary_key=True)
    window_start: Mapped[datetime] = mapped_column(primary_key=True)
    held_cost_micros: Mapped[int] = mapped_column(BigInteger)
    held_tokens: Mapped[int] = mapped_column(BigInteger)
    spent_cost_micros: Mapped[int] = mapped_column(BigInteger)
    spent_tokens: Mapped[int] = mapped_column(BigInteger)
