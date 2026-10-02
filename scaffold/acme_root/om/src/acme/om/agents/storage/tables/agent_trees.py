from datetime import datetime

from sqlalchemy.orm import Mapped

from acme.om.storage.tables.base import Base, IdentifiableMixin, TrackableMixin


class AgentTrees(IdentifiableMixin, TrackableMixin, Base):
    """One row per tree, keyed by its root session's id, read by that id
    alone."""

    __tablename__ = "agent_trees"
    height: Mapped[int]
    count: Mapped[int]
    concurrency: Mapped[int | None]
    deadline: Mapped[datetime | None]
    size: Mapped[int]
    version: Mapped[int]
