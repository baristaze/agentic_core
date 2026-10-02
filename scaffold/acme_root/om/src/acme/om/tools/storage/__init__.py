"""Storage of the tools swimlane: each tenant's layer of policy, one row a
tenant. Every operation takes org_id first. A write after the create is a
compare-and-set on the version, landed with its outbox rows in one
commit."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.outbox.types.row import OutboxRow
from acme.om.tools.types.policy import ToolPolicy


class ToolStorageInterface(ABC):
    @abstractmethod
    async def read_policy(self, org_id: UUID) -> ToolPolicy | None:
        """The tenant's policy, or None when it has never written one."""
        ...

    @abstractmethod
    async def create_policy(
        self, org_id: UUID, policy: ToolPolicy, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        """The create, with the rows that announce it, in one commit; False,
        with nothing landed, when the id is written already.
        `UniqueKeyTaken` when the tenant holds another policy already."""
        ...

    @abstractmethod
    async def write_policy(
        self,
        org_id: UUID,
        policy: ToolPolicy,
        expected_version: int,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> None:
        """The compare-and-set: lands the policy and its outbox rows together
        when the stored one is at `expected_version`, and raises
        `PreconditionFailed` otherwise, landing nothing."""
        ...

    @abstractmethod
    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        """At most `limit` policies of a deleted tenant past its retention;
        returns how many went."""
        ...
