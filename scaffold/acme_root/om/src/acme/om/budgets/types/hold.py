"""A hold and how it settles.

A hold reserves a call's worst case on every budget line the call is charged
to, before the call. It is written once. When the call is over, a
settlement closes it, also written once: it releases the hold only when the
provider provably did not bill, and otherwise counts the usage the provider
reported, or usage retrieved later, else the whole hold. A line's tally per
window holds what open holds reserve and what settlements spent."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from acme.om.base import Created, Identifiable, Platform
from acme.om.budgets.types.amount import Amount, Spend
from acme.om.budgets.types.budget import MAX_KEY, BudgetScope
from acme.om.steps.types.content import Stored

MAX_SCOPES = 16
"""The scopes one call is charged to, at most: a session, its tree, the
person who pays, a project, a team, the tenant, and room beside them."""


class HoldRequest(Platform):
    """What the engine asks the gate before a model call or a job that spends:
    who pays, the scopes it is charged to, and its worst case."""

    spender_id: UUID | None  # None when the engine cannot tell who pays: nothing is spent
    scopes: tuple[BudgetScope, ...] = Field(max_length=MAX_SCOPES)
    exposure: Spend  # the worst case, from `budgets.rules.call_exposure` or `job_exposure`
    own: Amount | None = None  # the request's own amount, whose window is the request
    session_id: UUID | None = None  # the session the call serves, when one does
    purpose: Stored = Field(min_length=1, max_length=MAX_KEY)  # a model role, or a job's kind


class HoldLine(Platform):
    """One budget a hold is charged to, as the gate found it: its window at
    the time of the call, and its amount then."""

    budget_id: UUID
    scope: BudgetScope
    window_start: datetime
    resets_at: datetime | None  # None for a window that never resets
    amount: Amount


class Hold(Identifiable, Created):
    """A call's worst case, reserved on every line before the call. Written
    once; a settlement closes it."""

    spender_id: UUID
    session_id: UUID | None = None
    purpose: Stored = Field(min_length=1, max_length=MAX_KEY)
    exposure: Spend
    own: Amount | None = None
    lines: tuple[HoldLine, ...] = ()


class NotBilledProof(StrEnum):
    """Why the engine knows the provider billed nothing. Nothing else
    releases a hold."""

    REFUSED_BEFORE_PROCESSING = "refused_before_processing"  # answered before reading the request
    NEVER_SENT = "never_sent"  # no connection carried the request


class NotBilled(Platform):
    kind: Literal["not_billed"] = "not_billed"
    proof: NotBilledProof


class Billed(Platform):
    """The usage the provider reported with its response, or that was
    retrieved later, priced at list price."""

    kind: Literal["billed"] = "billed"
    usage: Spend


class BillUnknown(Platform):
    """A call sent and never answered with its usage: a broken stream, or a
    crash after the send. It is usually billed, so the whole hold counts."""

    kind: Literal["unknown"] = "unknown"


Bill = Annotated[NotBilled | Billed | BillUnknown, Field(discriminator="kind")]


class Settlement(Identifiable, Created):
    """How a hold closed: what the bill was, what it spent, and by how much
    the spend passed the hold, when it did. Written once per hold."""

    hold_id: UUID
    bill: Bill
    spent: Spend
    overshoot: Spend | None = None  # recorded and alarmed, never absorbed


class Tally(Platform):
    """One budget's count in one window: what open holds reserve, and what
    settlements spent. A cost no price gave counts as nothing here: the gate
    refuses such a call on every line that bounds cost."""

    budget_id: UUID
    window_start: datetime
    held_cost_micros: int = Field(default=0, ge=0)
    held_tokens: int = Field(default=0, ge=0)
    spent_cost_micros: int = Field(default=0, ge=0)
    spent_tokens: int = Field(default=0, ge=0)
