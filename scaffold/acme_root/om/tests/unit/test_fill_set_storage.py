import pytest
from contracts.fill_set_storage import FillSetStorageContract

from acme.om.models.storage import FillSetStorageInterface
from acme.om.models.storage.impl.memory import FillSetStorageMemoryImpl


class TestFillSetStorageMemory(FillSetStorageContract):
    @pytest.fixture
    def storage(self) -> FillSetStorageInterface:
        return FillSetStorageMemoryImpl()
