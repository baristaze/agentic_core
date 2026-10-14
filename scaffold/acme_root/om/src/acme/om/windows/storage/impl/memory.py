from collections.abc import Sequence
from uuid import UUID

from acme.om.storage.impl.memory_base import MemoryStorageBase, MemoryTable
from acme.om.windows.storage import WindowStorageInterface
from acme.om.windows.types.tool_output import ToolOutput


class WindowStorageMemoryImpl(MemoryStorageBase, WindowStorageInterface):
    def __init__(self) -> None:
        super().__init__()
        self._tool_outputs: MemoryTable[ToolOutput] = {}

    async def write_tool_output(self, org_id: UUID, tool_output: ToolOutput) -> bool:
        async with self._lock:
            return self._insert(self._tool_outputs, org_id, tool_output)

    async def read_tool_output(
        self, org_id: UUID, agent_session_id: UUID, tool_output_id: UUID
    ) -> ToolOutput | None:
        found = self._get(self._tool_outputs, org_id, tool_output_id)
        return found if found is not None and found.agent_session_id == agent_session_id else None

    async def read_tool_outputs(
        self, org_id: UUID, agent_session_id: UUID | None, limit: int
    ) -> list[ToolOutput]:
        found = [
            tool_output
            for tool_output in self._rows(self._tool_outputs, org_id)
            if agent_session_id is None or tool_output.agent_session_id == agent_session_id
        ]
        return found[:limit]

    async def purge_tool_outputs(self, org_id: UUID, tool_output_ids: Sequence[UUID]) -> int:
        async with self._lock:
            gone = [i for i in set(tool_output_ids) if self._get(self._tool_outputs, org_id, i)]
            for tool_output_id in gone:
                del self._tool_outputs[tool_output_id]
            return len(gone)
