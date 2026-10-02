"""A tree: a root session and its sub-agents. One record, keyed by the
root's id, holds what the whole tree shares, so a child reads its tree's
bounds and never holds a copy of its own.

The root's budget bounds every call of the tree, and the tree's id is the
key its budget scope takes: a child draws on what the tree has left and
never creates budget. The deadline is one instant the whole tree shares,
and moving it moves it for every session of the tree."""

from datetime import datetime
from typing import ClassVar

from pydantic import Field

from acme.om.base import Identifiable, Trackable


class AgentTree(Identifiable, Trackable):
    MANAGER_OWNED_FIELDS: ClassVar[tuple[str, ...]] = ("size", "version")
    """A spawn takes a slot; every write is a compare-and-set."""

    height: int = Field(ge=1)  # 1 is a single agent
    count: int = Field(ge=0)  # the most sub-agents besides the root
    concurrency: int | None = Field(default=None, ge=1)  # the most that run at once, when set
    deadline: datetime | None = None  # one instant the whole tree shares
    size: int = Field(default=0, ge=0)  # the sub-agents spawned so far
    version: int = Field(default=1, ge=1)
