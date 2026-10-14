"""Storage of the budgets swimlane. Every operation takes org_id first.

Two interfaces, one per role. The budgets themselves are the system of
record, in `core`, written with their outbox rows. The ledger is in
`activity`: a hold and its settlement are each written once, and a tally per
line and window counts what open holds reserve and what settlements spent
(ADR 1006). A hold carries the amount of each line as the gate read it, so
the ledger never reads `core`. Beside them, a usage record per billed model
call keeps what the call used and cost, with no content, in every storage
mode (ADR 1014)."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from acme.om.outbox.types.row import OutboxRow
from acme.om.spend_budgets.types.spend_breach import Refusal
from acme.om.spend_budgets.types.spend_budget import SpendBudget, SpendBudgetScope
from acme.om.spend_budgets.types.spend_hold import SpendHold, SpendSettlement, SpendTally
from acme.om.spend_budgets.types.usage import LoopUsage, UsageRecord, UsageRollup


class SpendBudgetStorageInterface(ABC):
    @abstractmethod
    async def create_spend_budget(
        self, org_id: UUID, budget: SpendBudget, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        """The create, with the rows that announce it, in one commit; False,
        with nothing landed, when the id is written already."""
        ...

    @abstractmethod
    async def read_spend_budget(
        self, org_id: UUID, spend_budget_id: UUID
    ) -> SpendBudget | None: ...

    @abstractmethod
    async def read_spend_budgets(
        self, org_id: UUID, after: UUID | None, limit: int
    ) -> list[SpendBudget]:
        """The tenant's budgets by id, strictly after `after`, at most `limit`."""
        ...

    @abstractmethod
    async def read_spend_budgets_for(
        self, org_id: UUID, scopes: Sequence[SpendBudgetScope], limit: int
    ) -> list[SpendBudget]:
        """The tenant's open budgets whose scope is one of `scopes`, by id, at
        most `limit`: what binds a call charged to them. A closed budget binds
        nothing, so it is neither read nor counted toward `limit`."""
        ...

    @abstractmethod
    async def write_spend_budget(
        self,
        org_id: UUID,
        budget: SpendBudget,
        expected_version: int,
        outbox_rows: tuple[OutboxRow, ...],
    ) -> None:
        """The compare-and-set: lands the budget and its outbox rows together
        when the stored one is at `expected_version`, and raises
        `PreconditionFailed` otherwise, landing nothing."""
        ...

    @abstractmethod
    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        """At most `limit` budgets of a deleted tenant past its retention;
        returns how many went."""
        ...


class LedgerStorageInterface(ABC):
    @abstractmethod
    async def open_spend_hold(self, org_id: UUID, hold: SpendHold) -> Refusal | None:
        """The gate's one write, in one transaction: the tally of each of the
        hold's lines, made when its window has none, is locked in the order of
        the lines; the hold is held against every line beside what the window
        spent and holds already, and against its own amount
        (`spend_budgets.rules.breaches`). When it fits, its worst case joins every
        line's held amount and the hold is written, and None is returned.
        Otherwise nothing is written, and the refusal lists every breach. Two
        holds over one line queue on its tally, so they never both pass when
        only one fits. A hold opened already answers None and changes
        nothing; an id another tenant holds is `TenantMismatch`."""
        ...

    @abstractmethod
    async def read_hold(self, org_id: UUID, spend_hold_id: UUID) -> SpendHold | None: ...

    @abstractmethod
    async def close_spend_hold(self, org_id: UUID, settlement: SpendSettlement) -> SpendSettlement:
        """The settlement of the hold it names, in one transaction with the
        tallies of the hold's lines, locked in the order of the lines: the
        hold's worst case leaves each line's held amount and what it spent
        joins the window's. A hold closes once: one closed already answers its
        first settlement, and nothing moves. A hold the tenant does not hold
        is `NotFound`, and nothing moves."""
        ...

    @abstractmethod
    async def read_spend_settlement(
        self, org_id: UUID, spend_hold_id: UUID
    ) -> SpendSettlement | None: ...

    @abstractmethod
    async def read_spend_tally(
        self, org_id: UUID, spend_budget_id: UUID, window_start: datetime
    ) -> SpendTally | None:
        """One line's count in one window, or None while nothing was held there."""
        ...

    @abstractmethod
    async def append_usage_record(self, org_id: UUID, record: UsageRecord) -> bool:
        """Writes one call's usage record, once: False, with nothing written,
        when the tenant holds a record of its hold already; an id another
        tenant holds is `TenantMismatch`."""
        ...

    @abstractmethod
    async def read_usage_records(
        self, org_id: UUID, agent_session_id: UUID, after: UUID | None, limit: int
    ) -> list[UsageRecord]:
        """A page of a session's records, in the order they were written (by
        id), after the id named."""
        ...

    @abstractmethod
    async def read_usage_rollups(
        self, org_id: UUID, agent_session_id: UUID, limit: int
    ) -> list[LoopUsage]:
        """The session's rollups, one per loop, of the first `limit` loops in
        the order each loop's first record was written."""
        ...

    @abstractmethod
    async def read_usage_total(self, org_id: UUID, agent_session_id: UUID) -> UsageRollup:
        """The rollup of every record of the session: no calls when it has
        none."""
        ...

    @abstractmethod
    async def count_tenant(self, org_id: UUID, limit: int) -> int:
        """How many holds, settlements, and tallies the tenant keeps, counted
        up to `limit` and no further: what stays of a deleted tenant's
        ledger, which no serving login deletes. Its usage records are not
        counted: they never keep a tenant from being marked purged (ADR
        1014)."""
        ...
