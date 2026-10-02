import pytest
from contracts.budget_storage import BudgetStorageContract

from acme.om.budgets.storage import BudgetStorageInterface
from acme.om.budgets.storage.impl.memory import BudgetStorageMemoryImpl


class TestBudgetStorageMemory(BudgetStorageContract):
    @pytest.fixture
    def storage(self) -> BudgetStorageInterface:
        return BudgetStorageMemoryImpl()
