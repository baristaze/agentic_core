import pytest

from acme.om.agent_sessions.storage import AgentSessionStorageInterface
from acme.om.agent_sessions.storage.impl.memory import AgentSessionStorageMemoryImpl

from contracts.agent_session_storage import AgentSessionStorageContract


class TestAgentSessionStorageMemory(AgentSessionStorageContract):
    @pytest.fixture
    def storage(self) -> AgentSessionStorageInterface:
        return AgentSessionStorageMemoryImpl()
