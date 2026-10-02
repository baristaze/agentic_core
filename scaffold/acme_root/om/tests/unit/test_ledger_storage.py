import pytest
from contracts.ledger_storage import LedgerStorageContract

from acme.om.budgets.storage import LedgerStorageInterface
from acme.om.budgets.storage.impl.memory import LedgerStorageMemoryImpl


class TestLedgerStorageMemory(LedgerStorageContract):
    @pytest.fixture
    def storage(self) -> LedgerStorageInterface:
        return LedgerStorageMemoryImpl()
