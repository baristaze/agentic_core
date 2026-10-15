import pytest

from acme.om.privacy.storage import PrivacyStorageInterface
from acme.om.privacy.storage.impl.postgres import PrivacyStoragePostgresImpl
from acme.om.storage.impl.pg_base import LoginSessions

from contracts.privacy_storage import PrivacyStorageContract

pytestmark = pytest.mark.integration


class TestPrivacyStoragePostgres(PrivacyStorageContract):
    @pytest.fixture
    def storage(self, pg_sessions: LoginSessions) -> PrivacyStorageInterface:
        return PrivacyStoragePostgresImpl(pg_sessions)
