import pytest
from contracts.tool_storage import ToolStorageContract

from acme.om.storage.impl.pg_base import LoginSessions
from acme.om.tools.storage import ToolStorageInterface
from acme.om.tools.storage.impl.postgres import ToolStoragePostgresImpl

pytestmark = pytest.mark.integration


class TestToolStoragePostgres(ToolStorageContract):
    @pytest.fixture
    def storage(self, pg_sessions: LoginSessions) -> ToolStorageInterface:
        return ToolStoragePostgresImpl(pg_sessions)
