import pytest
from contracts.step_storage import StepStorageContract

from acme.om.steps.storage import StepStorageInterface
from acme.om.steps.storage.impl.memory import StepStorageMemoryImpl


class TestStepStorageMemory(StepStorageContract):
    @pytest.fixture
    def storage(self) -> StepStorageInterface:
        return StepStorageMemoryImpl()
