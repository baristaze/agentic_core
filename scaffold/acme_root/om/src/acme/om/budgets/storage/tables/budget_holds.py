from typing import Any
from uuid import UUID

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from acme.om.storage.tables.base import Base, CreatedMixin, IdentifiableMixin


class BudgetHolds(IdentifiableMixin, CreatedMixin, Base):
    """A call's worst case, reserved on its lines before the call: one row per
    hold, written once. The serving logins hold SELECT and INSERT here and
    nothing more (ADR 1006)."""

    __tablename__ = "budget_holds"
    spender_id: Mapped[UUID]
    session_id: Mapped[UUID | None]
    purpose: Mapped[str]
    exposure: Mapped[dict[str, Any]]
    own: Mapped[dict[str, Any] | None]
    # Each line as the gate read it: its budget, scope, window, and amount.
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
