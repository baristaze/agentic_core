import pytest

from acme.om.spend_budgets.storage import LedgerStorageInterface
from acme.om.spend_budgets.storage.impl.memory import LedgerStorageMemoryImpl

from contracts.ledger_storage import LedgerStorageContract


class TestLedgerStorageMemory(LedgerStorageContract):
    @pytest.fixture
    def storage(self) -> LedgerStorageInterface:
        return LedgerStorageMemoryImpl()
