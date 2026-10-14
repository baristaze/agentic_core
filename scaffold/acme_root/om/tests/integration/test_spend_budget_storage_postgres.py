import pytest
from contracts.spend_budget_storage import SpendBudgetStorageContract

from acme.om.spend_budgets.storage import SpendBudgetStorageInterface
from acme.om.spend_budgets.storage.impl.postgres import SpendBudgetStoragePostgresImpl
from acme.om.storage.impl.pg_base import LoginSessions

pytestmark = pytest.mark.integration


class TestSpendBudgetStoragePostgres(SpendBudgetStorageContract):
    @pytest.fixture
    def storage(self, pg_sessions: LoginSessions) -> SpendBudgetStorageInterface:
        return SpendBudgetStoragePostgresImpl(pg_sessions)
