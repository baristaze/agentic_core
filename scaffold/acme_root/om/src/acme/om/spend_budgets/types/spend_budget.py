"""A budget: a scope, a window, and an amount.

The scope is a key the platform defines (a session, a tree, a person, a
project, a team, a tenant); the engine compares keys and reads nothing into
them. The window is the scope's whole life, an hour, a day, a week, a month,
or a span of whole seconds, each counted in UTC. The amount is reference
cost, native tokens, or both. Each part is a column of its own, so the gate
finds a scope's budgets by its key in the statement.

A budget may be closed, once and for good: from then on it binds no call,
and what it counted stays. So a budget made on a session's scope just before
a run and closed when the run ends counts that run alone (ADR 1036)."""

from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Self

from pydantic import Field, model_validator

from acme.om.agent_steps.types.content import Stored
from acme.om.base import Identifiable, OmBase, Trackable
from acme.om.spend_budgets.types.amount import Amount

MAX_KEY = 200


class SpendBudgetScopeKind(StrEnum):
    AGENT_SESSION = "agent_session"
    TREE = "tree"
    PERSON = "person"
    AGENT_PROJECT = "agent_project"
    TEAM = "team"
    TENANT = "tenant"


class SpendBudgetScope(OmBase):
    """One scope a call is charged to: its kind and the key the platform
    gives it, such as a session's id or a project's name."""

    kind: SpendBudgetScopeKind
    key: Stored = Field(min_length=1, max_length=MAX_KEY)


class WindowKind(StrEnum):
    LIFE = "life"  # the scope's whole life: it never resets
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"  # from Monday
    MONTH = "month"
    SPAN = "span"  # a custom span of whole seconds, counted from the Unix epoch


class SpendBudgetWindow(OmBase):
    kind: WindowKind
    seconds: int | None = Field(default=None, ge=1)  # a span's length, and only a span's

    @model_validator(mode="after")
    def _a_span_has_a_length(self) -> Self:
        if (self.seconds is not None) != (self.kind is WindowKind.SPAN):
            raise ValueError("a span window has a length in seconds, and no other window has")
        return self


class SpendBudget(Identifiable, Trackable):
    MANAGER_OWNED_FIELDS: ClassVar[tuple[str, ...]] = ("version", "closed_at")
    """Every change of the amount, and the close, is a compare-and-set on the
    version. Only `close_spend_budget` sets `closed_at`."""

    scope_kind: SpendBudgetScopeKind
    scope_key: Stored = Field(min_length=1, max_length=MAX_KEY)
    window_kind: WindowKind
    window_seconds: int | None = Field(default=None, ge=1)
    cost_micros: int | None = Field(default=None, ge=0)
    tokens: int | None = Field(default=None, ge=0)
    version: int = Field(default=1, ge=1)
    closed_at: datetime | None = None  # set once by its close; a closed budget binds no call

    @model_validator(mode="after")
    def _a_window_and_an_amount(self) -> Self:
        # Each value object checks its own part.
        SpendBudgetWindow(kind=self.window_kind, seconds=self.window_seconds)
        Amount(cost_micros=self.cost_micros, tokens=self.tokens)
        return self

    @property
    def scope(self) -> SpendBudgetScope:
        return SpendBudgetScope(kind=self.scope_kind, key=self.scope_key)

    @property
    def window(self) -> SpendBudgetWindow:
        return SpendBudgetWindow(kind=self.window_kind, seconds=self.window_seconds)

    @property
    def amount(self) -> Amount:
        return Amount(cost_micros=self.cost_micros, tokens=self.tokens)


class SpendBudgetPage(OmBase):
    items: tuple[SpendBudget, ...]
    has_more: bool
