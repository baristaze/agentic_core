import pytest
from contracts.attribution_storage import AttributionStorageContract

from acme.om.attribution.storage import AttributionStorageInterface
from acme.om.attribution.storage.impl.memory import AttributionStorageMemoryImpl


class TestAttributionStorageMemory(AttributionStorageContract):
    @pytest.fixture
    def storage(self) -> AttributionStorageInterface:
        return AttributionStorageMemoryImpl()
