import pytest

from acme.om.language_models.storage import FillSetStorageInterface
from acme.om.language_models.storage.impl.memory import FillSetStorageMemoryImpl

from contracts.fill_set_storage import FillSetStorageContract


class TestFillSetStorageMemory(FillSetStorageContract):
    @pytest.fixture
    def storage(self) -> FillSetStorageInterface:
        return FillSetStorageMemoryImpl()
