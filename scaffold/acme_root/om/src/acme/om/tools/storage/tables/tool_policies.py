from typing import Any

from sqlalchemy import Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from acme.om.storage.tables.base import Base, IdentifiableMixin, TrackableMixin


class ToolPolicies(IdentifiableMixin, TrackableMixin, Base):
    """Each tenant's layer of tool policy: one row a tenant, which the unique
    index on org_id holds, so it gets no index of its own."""

    __tablename__ = "tool_policies"
    __org_id_index__ = False
    __table_args__ = (Index("uq_tool_policies_org_id", "org_id", unique=True),)
    rules: Mapped[list[dict[str, Any]]] = mapped_column(JSONB())
    approvers: Mapped[list[dict[str, Any]]] = mapped_column(JSONB())
    version: Mapped[int]
