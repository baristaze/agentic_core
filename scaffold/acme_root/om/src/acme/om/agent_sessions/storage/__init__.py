"""Storage of the agent sessions swimlane. Every operation takes org_id
first, but the sweep's one read across tenants. A session's every write
after the create is a compare-and-set on its version, landed with its outbox
rows in one commit. Its delete, the purge, runs under the purge login, which
no serving process holds (ADR 1010).

A session's title is kept sealed, never in the clear: the create writes it,
and no compare-and-set touches it. The one write after is the sweep's, which
seals in place a title stored before titles were sealed (ADR 1042)."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from acme.om.agent_sessions.types.agent_session import AgentSession, AgentSessionStatus, Among
from acme.om.context import tenantless
from acme.om.outbox.types.row import OutboxRow


class AgentSessionStorageInterface(ABC):
    @abstractmethod
    async def create_agent_session(
        self, org_id: UUID, session: AgentSession, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        """The create, with the rows that announce it, in one commit; False,
        with nothing landed, when the id is written already. Its title is
        sealed or absent: a plain one is refused (`ValueError`), since only
        the manager seals it."""
        ...

    @abstractmethod
    async def read_agent_session(
        self, org_id: UUID, agent_session_id: UUID
    ) -> AgentSession | None: ...

    @abstractmethod
    async def read_children(
        self, org_id: UUID, parent_id: UUID, after: UUID | None, limit: int
    ) -> list[AgentSession]:
        """The sessions `parent_id` spawned, by id, strictly after `after`,
        at most `limit` of them."""
        ...

    @abstractmethod
    async def read_agent_sessions(
        self,
        org_id: UUID,
        status: AgentSessionStatus | None,
        after: UUID | None,
        limit: int,
        *,
        among: Among | None = None,
        person: UUID | None = None,
    ) -> list[AgentSession]:
        """The tenant's sessions in a status, or in any, by id, strictly
        after `after`, at most `limit` of them; a session marked deleted is
        none of them. With `among`, only roots: the ones `person` made or
        takes part in (`MINE`), and with them every org-wide one
        (`VISIBLE`), or every root when `person` is None."""
        ...

    @abstractmethod
    async def write_agent_session(
        self,
        org_id: UUID,
        session: AgentSession,
        expected_version: int,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> None:
        """The compare-and-set: lands the session and its outbox rows
        together when the stored one is at `expected_version`, and raises
        `PreconditionFailed` otherwise, landing nothing. The stored title
        stays as it is, whatever `session` holds."""
        ...

    @abstractmethod
    @tenantless
    async def read_clear_titles(self, limit: int) -> list[tuple[UUID, AgentSession]]:
        """Cross-tenant, for the sweep, in the system scope: at most `limit`
        sessions whose title is stored in the clear, from before titles were
        sealed, whatever their tenant, each with its tenant, in no order.
        Once every title is sealed, it finds none."""
        ...

    @abstractmethod
    async def seal_title(self, org_id: UUID, session: AgentSession, expected_version: int) -> bool:
        """The sweep's write of a title stored in the clear: `session`'s
        title, sealed or absent, lands with its version and its update when
        the stored one is at `expected_version` and its title is still in
        the clear. False, landing nothing, otherwise. The version moves, so
        a writer that read the clear title before cannot write it back."""
        ...

    @abstractmethod
    @tenantless
    async def read_purgeable(
        self, deleted_before: datetime, limit: int
    ) -> list[tuple[UUID, AgentSession]]:
        """Cross-tenant, for the sweep, in the system scope: at most `limit`
        sessions marked deleted before the cut, whatever their tenant, each
        with its tenant, in no order: the sessions whose retention has
        ended, claimed for their purge or not yet. One read a pass for every
        tenant, so a tenant with nothing to purge costs nothing."""
        ...

    @abstractmethod
    async def tree_holds_others(self, org_id: UUID, root_id: UUID, agent_session_id: UUID) -> bool:
        """Whether the tree `root_id` holds a session besides `agent_session_id`,
        marked deleted or not."""
        ...

    @abstractmethod
    async def purge_agent_session(self, org_id: UUID, agent_session_id: UUID) -> bool:
        """Deletes the session's row when it is claimed for its purge, under
        the purge login; False, with nothing deleted, for a session that is
        not claimed, another tenant's, or gone."""
        ...

    @abstractmethod
    async def read_tenant_agent_sessions(self, org_id: UUID, limit: int) -> list[UUID]:
        """The ids of at most `limit` sessions of the tenant, marked deleted
        or not, in no order: one batch of a deleted tenant's purge."""
        ...

    @abstractmethod
    async def purge_tenant(self, org_id: UUID, agent_session_ids: Sequence[UUID]) -> int:
        """Deletes the rows of exactly `agent_session_ids` that the tenant holds,
        under the purge login, so a session read since stays; an id of
        another tenant's, or one gone, deletes nothing. Returns how many
        went."""
        ...
