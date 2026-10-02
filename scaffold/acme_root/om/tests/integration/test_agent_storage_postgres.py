import pytest
from contracts.agent_storage import AgentStorageContract

from acme.om.agents.storage import AgentStorageInterface
from acme.om.agents.storage.impl.postgres import AgentStoragePostgresImpl
from acme.om.storage.impl.pg_base import LoginSessions

pytestmark = pytest.mark.integration


class TestAgentStoragePostgres(AgentStorageContract):
    @pytest.fixture
    def storage(self, pg_sessions: LoginSessions) -> AgentStorageInterface:
        return AgentStoragePostgresImpl(pg_sessions)
