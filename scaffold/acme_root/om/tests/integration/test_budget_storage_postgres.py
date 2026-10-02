import pytest
from contracts.budget_storage import BudgetStorageContract

from acme.om.budgets.storage import BudgetStorageInterface
from acme.om.budgets.storage.impl.postgres import BudgetStoragePostgresImpl
from acme.om.storage.impl.pg_base import LoginSessions

pytestmark = pytest.mark.integration


class TestBudgetStoragePostgres(BudgetStorageContract):
    @pytest.fixture
    def storage(self, pg_sessions: LoginSessions) -> BudgetStorageInterface:
        return BudgetStoragePostgresImpl(pg_sessions)
