import pytest

from acme.om.windows.storage import WindowStorageInterface
from acme.om.windows.storage.impl.memory import WindowStorageMemoryImpl

from contracts.window_storage import WindowStorageContract


class TestWindowStorageMemory(WindowStorageContract):
    @pytest.fixture
    def storage(self) -> WindowStorageInterface:
        return WindowStorageMemoryImpl()
