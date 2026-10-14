import pytest
from contracts.agent_step_storage import AgentStepStorageContract

from acme.om.agent_steps.storage import AgentStepStorageInterface
from acme.om.agent_steps.storage.impl.memory import AgentStepStorageMemoryImpl


class TestAgentStepStorageMemory(AgentStepStorageContract):
    @pytest.fixture
    def storage(self) -> AgentStepStorageInterface:
        return AgentStepStorageMemoryImpl()
