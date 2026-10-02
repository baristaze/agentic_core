import pytest
from contracts.agent_session_storage import AgentSessionStorageContract

from acme.om.agent_sessions.storage import AgentSessionStorageInterface
from acme.om.agent_sessions.storage.impl.postgres import AgentSessionStoragePostgresImpl
from acme.om.storage.impl.pg_base import LoginSessions

pytestmark = pytest.mark.integration


class TestAgentSessionStoragePostgres(AgentSessionStorageContract):
    @pytest.fixture
    def storage(self, pg_sessions: LoginSessions) -> AgentSessionStorageInterface:
        return AgentSessionStoragePostgresImpl(pg_sessions)
