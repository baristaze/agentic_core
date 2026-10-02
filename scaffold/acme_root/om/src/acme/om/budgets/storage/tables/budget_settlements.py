from typing import Any
from uuid import UUID

from sqlalchemy import Index
from sqlalchemy.orm import Mapped

from acme.om.storage.tables.base import Base, CreatedMixin, IdentifiableMixin


class BudgetSettlements(IdentifiableMixin, CreatedMixin, Base):
    """How each hold closed: one row per hold, written once. The serving
    logins hold SELECT and INSERT here and nothing more (ADR 1006)."""

    __tablename__ = "budget_settlements"
    # org_id leads the unique index, so it gets none of its own.
    __org_id_index__ = False
    __table_args__ = (
        # A hold closes once: a second settlement of it meets this index.
        Index("uq_budget_settlements_org_id_hold_id", "org_id", "hold_id", unique=True),
    )
    hold_id: Mapped[UUID]
    bill: Mapped[dict[str, Any]]
    spent: Mapped[dict[str, Any]]
    overshoot: Mapped[dict[str, Any] | None]
