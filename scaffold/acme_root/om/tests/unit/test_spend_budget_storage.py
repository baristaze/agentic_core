import pytest

from acme.om.spend_budgets.storage import SpendBudgetStorageInterface
from acme.om.spend_budgets.storage.impl.memory import SpendBudgetStorageMemoryImpl

from contracts.spend_budget_storage import SpendBudgetStorageContract


class TestSpendBudgetStorageMemory(SpendBudgetStorageContract):
    @pytest.fixture
    def storage(self) -> SpendBudgetStorageInterface:
        return SpendBudgetStorageMemoryImpl()
