"""Storage of the budgets swimlane. Every operation takes org_id first.

Two interfaces, one per role. The budgets themselves are the system of
record, in `core`, written with their outbox rows. The ledger is in
`activity`: a hold and its settlement are each written once, and a tally per
line and window counts what open holds reserve and what settlements spent
(ADR 1006). A hold carries the amount of each line as the gate read it, so
the ledger never reads `core`."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from acme.om.budgets.types.breach import Refusal
from acme.om.budgets.types.budget import Budget, BudgetScope
from acme.om.budgets.types.hold import Hold, Settlement, Tally
from acme.om.outbox.types.row import OutboxRow


class BudgetStorageInterface(ABC):
    @abstractmethod
    async def create_budget(
        self, org_id: UUID, budget: Budget, outbox_rows: tuple[OutboxRow, ...]
    ) -> bool:
        """The create, with the rows that announce it, in one commit; False,
        with nothing landed, when the id is written already."""
        ...

    @abstractmethod
    async def read_budget(self, org_id: UUID, budget_id: UUID) -> Budget | None: ...

    @abstractmethod
    async def read_budgets(self, org_id: UUID, after: UUID | None, limit: int) -> list[Budget]:
        """The tenant's budgets by id, strictly after `after`, at most `limit`."""
        ...

    @abstractmethod
    async def read_budgets_for(
        self, org_id: UUID, scopes: Sequence[BudgetScope], limit: int
    ) -> list[Budget]:
        """The tenant's budgets whose scope is one of `scopes`, by id, at most
        `limit`: what binds a call charged to them."""
        ...

    @abstractmethod
    async def write_budget(
        self,
        org_id: UUID,
        budget: Budget,
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
    async def open_hold(self, org_id: UUID, hold: Hold) -> Refusal | None:
        """The gate's one write, in one transaction: the tally of each of the
        hold's lines, made when its window has none, is locked in the order of
        the lines; the hold is held against every line beside what the window
        spent and holds already, and against its own amount
        (`budgets.rules.breaches`). When it fits, its worst case joins every
        line's held amount and the hold is written, and None is returned.
        Otherwise nothing is written, and the refusal lists every breach. Two
        holds over one line queue on its tally, so they never both pass when
        only one fits. A hold opened already answers None and changes
        nothing; an id another tenant holds is `TenantMismatch`."""
        ...

    @abstractmethod
    async def read_hold(self, org_id: UUID, hold_id: UUID) -> Hold | None: ...

    @abstractmethod
    async def close_hold(self, org_id: UUID, settlement: Settlement) -> Settlement:
        """The settlement of the hold it names, in one transaction with the
        tallies of the hold's lines, locked in the order of the lines: the
        hold's worst case leaves each line's held amount and what it spent
        joins the window's. A hold closes once: one closed already answers its
        first settlement, and nothing moves. A hold the tenant does not hold
        is `NotFound`, and nothing moves."""
        ...

    @abstractmethod
    async def read_settlement(self, org_id: UUID, hold_id: UUID) -> Settlement | None: ...

    @abstractmethod
    async def read_tally(
        self, org_id: UUID, budget_id: UUID, window_start: datetime
    ) -> Tally | None:
        """One line's count in one window, or None while nothing was held there."""
        ...

    @abstractmethod
    async def count_tenant(self, org_id: UUID, limit: int) -> int:
        """How many holds, settlements, and tallies the tenant keeps, counted
        up to `limit` and no further: what stays of a deleted tenant's
        ledger, which no serving login deletes."""
        ...
