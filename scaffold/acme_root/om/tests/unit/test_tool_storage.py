import pytest
from contracts.tool_storage import ToolStorageContract

from acme.om.tools.storage import ToolStorageInterface
from acme.om.tools.storage.impl.memory import ToolStorageMemoryImpl


class TestToolStorageMemory(ToolStorageContract):
    @pytest.fixture
    def storage(self) -> ToolStorageInterface:
        return ToolStorageMemoryImpl()
