import pytest
from contracts.spend_budget_storage import SpendBudgetStorageContract

from acme.om.spend_budgets.storage import SpendBudgetStorageInterface
from acme.om.spend_budgets.storage.impl.memory import SpendBudgetStorageMemoryImpl


class TestSpendBudgetStorageMemory(SpendBudgetStorageContract):
    @pytest.fixture
    def storage(self) -> SpendBudgetStorageInterface:
        return SpendBudgetStorageMemoryImpl()
