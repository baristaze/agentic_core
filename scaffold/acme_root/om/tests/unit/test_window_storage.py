import pytest
from contracts.window_storage import WindowStorageContract

from acme.om.windows.storage import WindowStorageInterface
from acme.om.windows.storage.impl.memory import WindowStorageMemoryImpl


class TestWindowStorageMemory(WindowStorageContract):
    @pytest.fixture
    def storage(self) -> WindowStorageInterface:
        return WindowStorageMemoryImpl()
