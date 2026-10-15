import pytest

from acme.om.agents.storage import AgentStorageInterface
from acme.om.agents.storage.impl.memory import AgentStorageMemoryImpl

from contracts.agent_storage import AgentStorageContract


class TestAgentStorageMemory(AgentStorageContract):
    @pytest.fixture
    def storage(self) -> AgentStorageInterface:
        return AgentStorageMemoryImpl()
