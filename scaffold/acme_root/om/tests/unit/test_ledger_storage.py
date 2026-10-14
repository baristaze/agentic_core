import pytest
from contracts.ledger_storage import LedgerStorageContract

from acme.om.spend_budgets.storage import LedgerStorageInterface
from acme.om.spend_budgets.storage.impl.memory import LedgerStorageMemoryImpl


class TestLedgerStorageMemory(LedgerStorageContract):
    @pytest.fixture
    def storage(self) -> LedgerStorageInterface:
        return LedgerStorageMemoryImpl()
