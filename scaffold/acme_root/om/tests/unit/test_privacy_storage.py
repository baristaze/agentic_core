import pytest

from acme.om.privacy.storage import PrivacyStorageInterface
from acme.om.privacy.storage.impl.memory import PrivacyStorageMemoryImpl

from contracts.privacy_storage import PrivacyStorageContract


class TestPrivacyStorageMemory(PrivacyStorageContract):
    @pytest.fixture
    def storage(self) -> PrivacyStorageInterface:
        return PrivacyStorageMemoryImpl()
