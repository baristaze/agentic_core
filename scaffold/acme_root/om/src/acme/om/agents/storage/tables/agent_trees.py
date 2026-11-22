from datetime import datetime

from sqlalchemy import Column, Integer
from sqlalchemy.orm import Mapped

from acme.om.storage.tables.base import Base, IdentifiableMixin, TrackableMixin


class AgentTrees(IdentifiableMixin, TrackableMixin, Base):
    """One row per tree, keyed by its root session's id, read by that id
    alone."""

    __tablename__ = "agent_trees"
    # `concurrency` is a dead column: the release before names it in every
    # insert, so it stays in the table, and no statement of this one names
    # it. A later release drops it (ADR 0038).
    __table_args__ = (Column("concurrency", Integer, nullable=True),)
    __mapper_args__ = {"exclude_properties": ["concurrency"]}
    height: Mapped[int]
    count: Mapped[int]
    deadline: Mapped[datetime | None]
    size: Mapped[int]
    version: Mapped[int]
