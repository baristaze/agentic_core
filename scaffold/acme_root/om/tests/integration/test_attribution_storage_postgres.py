import pytest
from contracts.attribution_storage import AttributionStorageContract

from acme.om.attribution.storage import AttributionStorageInterface
from acme.om.attribution.storage.impl.postgres import AttributionStoragePostgresImpl
from acme.om.storage.impl.pg_base import LoginSessions

pytestmark = pytest.mark.integration


class TestAttributionStoragePostgres(AttributionStorageContract):
    @pytest.fixture
    def storage(self, pg_sessions: LoginSessions) -> AttributionStorageInterface:
        return AttributionStoragePostgresImpl(pg_sessions)
