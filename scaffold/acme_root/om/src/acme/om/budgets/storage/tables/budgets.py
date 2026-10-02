from sqlalchemy import BigInteger, Index
from sqlalchemy.orm import Mapped, mapped_column

from acme.om.storage.tables.base import Base, IdentifiableMixin, TrackableMixin


class Budgets(IdentifiableMixin, TrackableMixin, Base):
    __tablename__ = "budgets"
    # org_id leads both indexes, so it gets none of its own.
    __org_id_index__ = False
    __table_args__ = (
        # The tenant's budgets by id, and the budgets of the scopes one call
        # is charged to, which the gate reads before every call.
        Index("ix_budgets_org_id_id", "org_id", "id"),
        Index("ix_budgets_org_id_scope_kind_scope_key", "org_id", "scope_kind", "scope_key"),
    )
    scope_kind: Mapped[str]
    scope_key: Mapped[str]
    window_kind: Mapped[str]
    window_seconds: Mapped[int | None]
    # Millionths of the reference currency, and native tokens: both pass
    # what an integer holds.
    cost_micros: Mapped[int | None] = mapped_column(BigInteger)
    tokens: Mapped[int | None] = mapped_column(BigInteger)
    version: Mapped[int]
