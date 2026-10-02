import pytest
from contracts.fill_set_storage import FillSetStorageContract

from acme.om.models.storage import FillSetStorageInterface
from acme.om.models.storage.impl.postgres import FillSetStoragePostgresImpl
from acme.om.storage.impl.pg_base import LoginSessions

pytestmark = pytest.mark.integration


class TestFillSetStoragePostgres(FillSetStorageContract):
    @pytest.fixture
    def storage(self, pg_sessions: LoginSessions) -> FillSetStorageInterface:
        return FillSetStoragePostgresImpl(pg_sessions)
