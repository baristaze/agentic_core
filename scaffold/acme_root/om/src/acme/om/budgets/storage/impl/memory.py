"""The in-memory budgets and ledger: the engine's own ledger, which counts in
one process. Every write reads and writes under one lock with no await in
between, which is its whole answer to two holds at once."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from acme.om.budgets.rules import TallyKey, closed, key_of, opened, refusal_of
from acme.om.budgets.storage import BudgetStorageInterface, LedgerStorageInterface
from acme.om.budgets.types.breach import Refusal
from acme.om.budgets.types.budget import Budget, BudgetScope
from acme.om.budgets.types.hold import Hold, Settlement, Tally
from acme.om.exceptions import NotFound, PreconditionFailed
from acme.om.outbox.storage import OutboxLandingInterface
from acme.om.outbox.types.row import OutboxRow
from acme.om.storage.impl.memory_base import MemoryStorageBase, MemoryTable


class BudgetStorageMemoryImpl(MemoryStorageBase, BudgetStorageInterface):
    def __init__(self, outbox: OutboxLandingInterface | None = None) -> None:
        super().__init__(outbox)
        self._budgets: MemoryTable[Budget] = {}

    async def create_budget(
        self, org_id: UUID, budget: Budget, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        async with self._lock:
            return self._insert(self._budgets, org_id, budget, outbox_rows)

    async def read_budget(self, org_id: UUID, budget_id: UUID) -> Budget | None:
        return self._get(self._budgets, org_id, budget_id)

    async def read_budgets(self, org_id: UUID, after: UUID | None, limit: int) -> list[Budget]:
        return [b for b in self._rows(self._budgets, org_id) if after is None or b.id > after][
            :limit
        ]

    async def read_budgets_for(
        self, org_id: UUID, scopes: Sequence[BudgetScope], limit: int
    ) -> list[Budget]:
        wanted = {(scope.kind, scope.key) for scope in scopes}
        return [
            b for b in self._rows(self._budgets, org_id) if (b.scope_kind, b.scope_key) in wanted
        ][:limit]

    async def write_budget(
        self,
        org_id: UUID,
        budget: Budget,
        expected_version: int,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> None:
        async with self._lock:
            # Another tenant's budget is no budget here, as the policy makes
            # it in Postgres: the write misses, and the snapshot is stale.
            found = self._get(self._budgets, org_id, budget.id)
            if found is None or found.version != expected_version:
                raise PreconditionFailed(
                    f"budget {budget.id} is no longer at version {expected_version}"
                )
            self._put(self._budgets, org_id, budget, outbox_rows)

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        async with self._lock:
            gone = [b.id for b in self._rows(self._budgets, org_id)][:limit]
            for budget_id in gone:
                del self._budgets[budget_id]
            return len(gone)


class LedgerStorageMemoryImpl(MemoryStorageBase, LedgerStorageInterface):
    def __init__(self) -> None:
        super().__init__()
        self._holds: MemoryTable[Hold] = {}
        self._settlements: dict[UUID, tuple[UUID, Settlement]] = {}  # by hold id
        self._tallies: dict[tuple[UUID, UUID, datetime], Tally] = {}

    async def open_hold(self, org_id: UUID, hold: Hold) -> Refusal | None:
        async with self._lock:
            if self._get(self._holds, org_id, hold.id) is not None:
                return None
            self._fence(self._holds, org_id, hold)
            tallies = self._tallies_of(org_id, hold)
            refusal = refusal_of(hold, tallies)
            if refusal is not None:
                return refusal
            for line in hold.lines:
                key = key_of(line)
                self._tallies[(org_id, *key)] = opened(tallies[key], hold.exposure)
            self._put(self._holds, org_id, hold)
            return None

    async def read_hold(self, org_id: UUID, hold_id: UUID) -> Hold | None:
        return self._get(self._holds, org_id, hold_id)

    async def close_hold(self, org_id: UUID, settlement: Settlement) -> Settlement:
        async with self._lock:
            hold = self._get(self._holds, org_id, settlement.hold_id)
            if hold is None:
                raise NotFound(f"hold {settlement.hold_id} not found")
            found = self._settlements.get(hold.id)
            if found is not None:
                return found[1]
            tallies = self._tallies_of(org_id, hold)
            for line in hold.lines:
                key = key_of(line)
                self._tallies[(org_id, *key)] = closed(
                    tallies[key], hold.exposure, settlement.spent
                )
            self._settlements[hold.id] = (org_id, settlement)
            return settlement

    async def read_settlement(self, org_id: UUID, hold_id: UUID) -> Settlement | None:
        found = self._settlements.get(hold_id)
        if found is None or found[0] != org_id:
            return None
        return found[1]

    async def read_tally(
        self, org_id: UUID, budget_id: UUID, window_start: datetime
    ) -> Tally | None:
        return self._tallies.get((org_id, budget_id, window_start))

    async def count_tenant(self, org_id: UUID, limit: int) -> int:
        holds = len(self._rows(self._holds, org_id))
        settlements = sum(1 for org, _ in self._settlements.values() if org == org_id)
        tallies = sum(1 for org, _, _ in self._tallies if org == org_id)
        return min(holds + settlements + tallies, limit)

    def _tallies_of(self, org_id: UUID, hold: Hold) -> dict[TallyKey, Tally]:
        """The tally of each line, a fresh one where the window has none."""
        return {
            key_of(line): self._tallies.get(
                (org_id, *key_of(line)),
                Tally(budget_id=line.budget_id, window_start=line.window_start),
            )
            for line in hold.lines
        }
