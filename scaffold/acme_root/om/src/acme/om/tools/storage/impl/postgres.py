from uuid import UUID

from sqlalchemy import select, update

from acme.om.exceptions import PreconditionFailed
from acme.om.outbox.storage.tables.outbox_rows import OutboxRows
from acme.om.outbox.types.row import OutboxRow
from acme.om.storage.impl.pg_base import PgStorageBase, delete_batch, deleted
from acme.om.storage.utils.translation import to_model, to_row, to_values
from acme.om.tools.storage import ToolStorageInterface
from acme.om.tools.storage.tables.tool_policies import ToolPolicies
from acme.om.tools.types.policy import ToolPolicy


class ToolStoragePostgresImpl(PgStorageBase, ToolStorageInterface):
    async def read_policy(self, org_id: UUID) -> ToolPolicy | None:
        stmt = select(ToolPolicies).where(ToolPolicies.org_id == org_id)
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, ToolPolicy)

    async def create_policy(
        self, org_id: UUID, policy: ToolPolicy, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        return await self._insert(ToolPolicies, org_id, policy, outbox_rows)

    async def write_policy(
        self,
        org_id: UUID,
        policy: ToolPolicy,
        expected_version: int,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> None:
        values = {k: v for k, v in to_values(policy, ToolPolicies).items() if k != "id"}
        # The version is in the WHERE, so two writers from one snapshot
        # cannot both land.
        stmt = (
            update(ToolPolicies)
            .where(
                ToolPolicies.id == policy.id,
                ToolPolicies.org_id == org_id,
                ToolPolicies.version == expected_version,
            )
            .values(**values)
            .returning(ToolPolicies.id)
        )
        async with self._session_for(ToolPolicies, org_id=org_id) as db:
            if (await db.execute(stmt)).scalar_one_or_none() is None:
                await db.rollback()
                raise PreconditionFailed(
                    f"tool policy {policy.id} is no longer at version {expected_version}"
                )
            for outbox_row in outbox_rows:
                db.add(to_row(outbox_row, OutboxRows, org_id=org_id))
            await db.commit()

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        stmt = delete_batch(ToolPolicies, ToolPolicies.org_id == org_id, limit=limit)
        async with self._session_for(stmt, org_id=org_id) as session:
            purged = deleted(await session.execute(stmt))
            await session.commit()
            return purged
