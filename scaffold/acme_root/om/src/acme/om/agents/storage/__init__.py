"""Storage of the agents swimlane: the trees. Every operation takes org_id
first. A tree's every write after the create is a compare-and-set on its
version; taking a slot for a child is one conditional statement."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.agents.types.tree import AgentTree
from acme.om.outbox.types.row import OutboxRow


class AgentTreeStorageInterface(ABC):
    @abstractmethod
    async def create_tree(
        self, org_id: UUID, tree: AgentTree, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        """The create, with the rows that announce it, in one commit; False,
        with nothing landed, when the id is written already."""
        ...

    @abstractmethod
    async def read_tree(self, org_id: UUID, tree_id: UUID) -> AgentTree | None: ...

    @abstractmethod
    async def take_slot(self, org_id: UUID, tree_id: UUID) -> AgentTree | None:
        """One more sub-agent, in one statement: the size moves one up, and
        the version with it, only while it is below the count. The tree as
        it is then, or None when it is full or not the tenant's."""
        ...

    @abstractmethod
    async def write_tree(
        self,
        org_id: UUID,
        tree: AgentTree,
        expected_version: int,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> None:
        """The compare-and-set: lands the tree and its outbox rows together
        when the stored one is at `expected_version`, and raises
        `PreconditionFailed` otherwise, landing nothing."""
        ...

    @abstractmethod
    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        """At most `limit` trees of a deleted tenant past its retention;
        returns how many went."""
        ...
