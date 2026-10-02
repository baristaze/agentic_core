from typing import Any

from sqlalchemy.orm import Mapped

from acme.om.storage.tables.base import Base, IdentifiableMixin, TrackableMixin


class SessionAuthorities(IdentifiableMixin, TrackableMixin, Base):
    """One row per session, keyed by the session's id, read by that id
    alone."""

    __tablename__ = "session_authorities"
    mode: Mapped[str]
    principal: Mapped[dict[str, Any]]
    spender: Mapped[dict[str, Any] | None]
    version: Mapped[int]
