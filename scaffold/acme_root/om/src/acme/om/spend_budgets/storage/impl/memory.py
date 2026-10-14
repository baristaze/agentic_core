"""The in-memory budgets and ledger: the engine's own ledger, which counts in
one process. Every write reads and writes under one lock with no await in
between, which is its whole answer to two holds at once."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from acme.om.exceptions import NotFound, PreconditionFailed, TenantMismatch
from acme.om.outbox.storage import OutboxLandingInterface
from acme.om.outbox.types.row import OutboxRow
from acme.om.spend_budgets.rules import (
    SpendTallyKey,
    closed,
    key_of,
    loop_rollups,
    opened,
    refusal_of,
    rollup_of,
)
from acme.om.spend_budgets.storage import LedgerStorageInterface, SpendBudgetStorageInterface
from acme.om.spend_budgets.types.spend_breach import Refusal
from acme.om.spend_budgets.types.spend_budget import SpendBudget, SpendBudgetScope
from acme.om.spend_budgets.types.spend_hold import SpendHold, SpendSettlement, SpendTally
from acme.om.spend_budgets.types.usage import LoopUsage, UsageRecord, UsageRollup
from acme.om.storage.impl.memory_base import MemoryStorageBase, MemoryTable


class SpendBudgetStorageMemoryImpl(MemoryStorageBase, SpendBudgetStorageInterface):
    def __init__(self, outbox: OutboxLandingInterface | None = None) -> None:
        super().__init__(outbox)
        self._budgets: MemoryTable[SpendBudget] = {}

    async def create_spend_budget(
        self, org_id: UUID, budget: SpendBudget, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        async with self._lock:
            return self._insert(self._budgets, org_id, budget, outbox_rows)

    async def read_spend_budget(self, org_id: UUID, spend_budget_id: UUID) -> SpendBudget | None:
        return self._get(self._budgets, org_id, spend_budget_id)

    async def read_spend_budgets(
        self, org_id: UUID, after: UUID | None, limit: int
    ) -> list[SpendBudget]:
        return [b for b in self._rows(self._budgets, org_id) if after is None or b.id > after][
            :limit
        ]

    async def read_spend_budgets_for(
        self, org_id: UUID, scopes: Sequence[SpendBudgetScope], limit: int
    ) -> list[SpendBudget]:
        wanted = {(scope.kind, scope.key) for scope in scopes}
        return [
            b
            for b in self._rows(self._budgets, org_id)
            if (b.scope_kind, b.scope_key) in wanted and b.closed_at is None
        ][:limit]

    async def write_spend_budget(
        self,
        org_id: UUID,
        budget: SpendBudget,
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
            for spend_budget_id in gone:
                del self._budgets[spend_budget_id]
            return len(gone)


class LedgerStorageMemoryImpl(MemoryStorageBase, LedgerStorageInterface):
    def __init__(self) -> None:
        super().__init__()
        self._holds: MemoryTable[SpendHold] = {}
        self._settlements: dict[UUID, tuple[UUID, SpendSettlement]] = {}  # by hold id
        self._tallies: dict[tuple[UUID, UUID, datetime], SpendTally] = {}
        self._usage: MemoryTable[UsageRecord] = {}

    async def open_spend_hold(self, org_id: UUID, hold: SpendHold) -> Refusal | None:
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

    async def read_hold(self, org_id: UUID, spend_hold_id: UUID) -> SpendHold | None:
        return self._get(self._holds, org_id, spend_hold_id)

    async def close_spend_hold(self, org_id: UUID, settlement: SpendSettlement) -> SpendSettlement:
        async with self._lock:
            hold = self._get(self._holds, org_id, settlement.spend_hold_id)
            if hold is None:
                raise NotFound(f"hold {settlement.spend_hold_id} not found")
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

    async def read_spend_settlement(
        self, org_id: UUID, spend_hold_id: UUID
    ) -> SpendSettlement | None:
        found = self._settlements.get(spend_hold_id)
        if found is None or found[0] != org_id:
            return None
        return found[1]

    async def read_spend_tally(
        self, org_id: UUID, spend_budget_id: UUID, window_start: datetime
    ) -> SpendTally | None:
        return self._tallies.get((org_id, spend_budget_id, window_start))

    async def append_usage_record(self, org_id: UUID, record: UsageRecord) -> bool:
        async with self._lock:
            found = self._usage.get(record.id)
            if found is not None and found[0] != org_id:
                raise TenantMismatch(f"usage record {record.id} is not in {org_id}")
            held = (r for org, r in self._usage.values() if org == org_id)
            if any(r.spend_hold_id == record.spend_hold_id for r in held):
                return False
            return self._insert(self._usage, org_id, record)

    async def read_usage_records(
        self, org_id: UUID, agent_session_id: UUID, after: UUID | None, limit: int
    ) -> list[UsageRecord]:
        records = self._session_usage(org_id, agent_session_id)
        return [r for r in records if after is None or r.id > after][:limit]

    async def read_usage_rollups(
        self, org_id: UUID, agent_session_id: UUID, limit: int
    ) -> list[LoopUsage]:
        return loop_rollups(self._session_usage(org_id, agent_session_id))[:limit]

    async def read_usage_total(self, org_id: UUID, agent_session_id: UUID) -> UsageRollup:
        return rollup_of(self._session_usage(org_id, agent_session_id))

    async def count_tenant(self, org_id: UUID, limit: int) -> int:
        holds = len(self._rows(self._holds, org_id))
        settlements = sum(1 for org, _ in self._settlements.values() if org == org_id)
        tallies = sum(1 for org, _, _ in self._tallies if org == org_id)
        return min(holds + settlements + tallies, limit)

    def _session_usage(self, org_id: UUID, agent_session_id: UUID) -> list[UsageRecord]:
        return [
            r for r in self._rows(self._usage, org_id) if r.agent_session_id == agent_session_id
        ]

    def _tallies_of(self, org_id: UUID, hold: SpendHold) -> dict[SpendTallyKey, SpendTally]:
        """The tally of each line, a fresh one where the window has none."""
        return {
            key_of(line): self._tallies.get(
                (org_id, *key_of(line)),
                SpendTally(spend_budget_id=line.spend_budget_id, window_start=line.window_start),
            )
            for line in hold.lines
        }
