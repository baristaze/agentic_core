from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, select

from acme.om.storage.impl.pg_base import PgStorageBase, deleted
from acme.om.storage.utils.translation import to_model
from acme.om.windows.storage import WindowStorageInterface
from acme.om.windows.storage.tables.tool_outputs import ToolOutputs
from acme.om.windows.types.tool_output import ToolOutput


class WindowStoragePostgresImpl(PgStorageBase, WindowStorageInterface):
    async def write_tool_output(self, org_id: UUID, tool_output: ToolOutput) -> bool:
        return await self._insert(ToolOutputs, org_id, tool_output)

    async def read_tool_output(
        self, org_id: UUID, agent_session_id: UUID, tool_output_id: UUID
    ) -> ToolOutput | None:
        stmt = select(ToolOutputs).where(
            ToolOutputs.org_id == org_id,
            ToolOutputs.agent_session_id == agent_session_id,
            ToolOutputs.id == tool_output_id,
        )
        async with self._session_for(stmt, org_id=org_id) as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return None if row is None else to_model(row, ToolOutput)

    async def read_tool_outputs(
        self, org_id: UUID, agent_session_id: UUID | None, limit: int
    ) -> list[ToolOutput]:
        stmt = select(ToolOutputs).where(ToolOutputs.org_id == org_id)
        if agent_session_id is not None:
            stmt = stmt.where(ToolOutputs.agent_session_id == agent_session_id)
        stmt = stmt.order_by(ToolOutputs.id).limit(limit)
        async with self._session_for(stmt, org_id=org_id) as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [to_model(row, ToolOutput) for row in rows]

    async def purge_tool_outputs(self, org_id: UUID, tool_output_ids: Sequence[UUID]) -> int:
        if not tool_output_ids:
            return 0
        stmt = delete(ToolOutputs).where(
            ToolOutputs.org_id == org_id, ToolOutputs.id.in_(list(tool_output_ids))
        )
        async with self._purge_session_for(stmt, org_id=org_id) as session:
            purged = deleted(await session.execute(stmt))
            await session.commit()
            return purged
