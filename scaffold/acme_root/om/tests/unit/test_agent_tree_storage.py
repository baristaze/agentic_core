import pytest
from contracts.agent_tree_storage import AgentTreeStorageContract

from acme.om.agents.storage import AgentTreeStorageInterface
from acme.om.agents.storage.impl.memory import AgentTreeStorageMemoryImpl


class TestAgentTreeStorageMemory(AgentTreeStorageContract):
    @pytest.fixture
    def storage(self) -> AgentTreeStorageInterface:
        return AgentTreeStorageMemoryImpl()
