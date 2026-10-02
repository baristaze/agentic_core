import pytest
from contracts.agent_session_storage import AgentSessionStorageContract

from acme.om.agent_sessions.storage import AgentSessionStorageInterface
from acme.om.agent_sessions.storage.impl.memory import AgentSessionStorageMemoryImpl


class TestAgentSessionStorageMemory(AgentSessionStorageContract):
    @pytest.fixture
    def storage(self) -> AgentSessionStorageInterface:
        return AgentSessionStorageMemoryImpl()
