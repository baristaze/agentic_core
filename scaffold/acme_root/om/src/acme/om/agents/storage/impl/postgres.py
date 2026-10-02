from uuid import UUID

from sqlalchemy import Update, delete, select, update

from acme.om.agents.storage import AgentStorageInterface
from acme.om.agents.storage.tables.agent_trees import AgentTrees
from acme.om.agents.types.tree import AgentTree
from acme.om.exceptions import PreconditionFailed
from acme.om.outbox.storage.tables.outbox_rows import OutboxRows
from acme.om.outbox.types.row import OutboxRow
from acme.om.storage.impl.pg_base import PgStorageBase, delete_batch, deleted
from acme.om.storage.utils.translation import to_model, to_row, to_values


def cas_statement(org_id: UUID, tree: AgentTree, expected_version: int) -> Update:
    """The compare-and-set of one tree: the version is in the WHERE, so two
    writers from one snapshot cannot both land. Returns the id when it
    hit."""
    values = {k: v for k, v in to_values(tree, AgentTrees).items() if k != "id"}
    return (
        update(AgentTrees)
        .where(
            AgentTrees.id == tree.id,
            AgentTrees.org_id == org_id,
            AgentTrees.version == expected_version,
        )
        .values(**values)
        .returning(AgentTrees.id)
    )


def slot_statement(org_id: UUID, tree_id: UUID) -> Update:
    """One more sub-agent while the tree has room: the count is in the
    WHERE, so two spawns at once queue on the row and the second reads the
    first one's size. Returns the tree as it is then."""
    return (
        update(AgentTrees)
        .where(
            AgentTrees.id == tree_id,
            AgentTrees.org_id == org_id,
            AgentTrees.size < AgentTrees.count,
        )
        .values(size=AgentTrees.size + 1, version=AgentTrees.version + 1)
        .returning(AgentTrees)
    )


class AgentStoragePostgresImpl(PgStorageBase, AgentStorageInterface):
    async def create_tree(
        self, org_id: UUID, tree: AgentTree, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        return await self._insert(AgentTrees, org_id, tree, outbox_rows)

    async def read_tree(self, org_id: UUID, tree_id: UUID) -> AgentTree | None:
        stmt = select(AgentTrees).where(AgentTrees.org_id == org_id, AgentTrees.id == tree_id)
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, AgentTree)

    async def take_slot(self, org_id: UUID, tree_id: UUID) -> AgentTree | None:
        stmt = slot_statement(org_id, tree_id)
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            taken = None if row is None else to_model(row, AgentTree)
            await session.commit()
            return taken

    async def write_tree(
        self,
        org_id: UUID,
        tree: AgentTree,
        expected_version: int,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> None:
        async with self._session_for(AgentTrees, org_id=org_id) as db:
            stmt = cas_statement(org_id, tree, expected_version)
            if (await db.execute(stmt)).scalar_one_or_none() is None:
                # Moved by another writer, or gone, or another tenant's: in
                # each the caller's snapshot is stale.
                await db.rollback()
                raise PreconditionFailed(
                    f"agent tree {tree.id} is no longer at version {expected_version}"
                )
            for outbox_row in outbox_rows:
                db.add(to_row(outbox_row, OutboxRows, org_id=org_id))
            await db.commit()

    async def purge_tree(self, org_id: UUID, tree_id: UUID) -> bool:
        stmt = delete(AgentTrees).where(AgentTrees.org_id == org_id, AgentTrees.id == tree_id)
        async with self._purge_session_for(stmt, org_id=org_id) as session:
            purged = deleted(await session.execute(stmt))
            await session.commit()
            return purged > 0

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        stmt = delete_batch(AgentTrees, AgentTrees.org_id == org_id, limit=limit)
        async with self._session_for(stmt, org_id=org_id) as session:
            purged = deleted(await session.execute(stmt))
            await session.commit()
            return purged
