import pytest

from acme.om.tools.storage import ToolStorageInterface
from acme.om.tools.storage.impl.memory import ToolStorageMemoryImpl

from contracts.tool_storage import ToolStorageContract


class TestToolStorageMemory(ToolStorageContract):
    @pytest.fixture
    def storage(self) -> ToolStorageInterface:
        return ToolStorageMemoryImpl()
