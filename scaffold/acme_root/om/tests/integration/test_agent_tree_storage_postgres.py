import pytest
from contracts.agent_tree_storage import AgentTreeStorageContract

from acme.om.agents.storage import AgentTreeStorageInterface
from acme.om.agents.storage.impl.postgres import AgentTreeStoragePostgresImpl
from acme.om.storage.impl.pg_base import LoginSessions

pytestmark = pytest.mark.integration


class TestAgentTreeStoragePostgres(AgentTreeStorageContract):
    @pytest.fixture
    def storage(self, pg_sessions: LoginSessions) -> AgentTreeStorageInterface:
        return AgentTreeStoragePostgresImpl(pg_sessions)
