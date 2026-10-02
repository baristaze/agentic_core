"""A budget: a scope, a window, and an amount.

The scope is a key the platform defines (a session, a tree, a person, a
project, a team, a tenant); the engine compares keys and reads nothing into
them. The window is the scope's whole life, an hour, a day, a week, a month,
or a span of whole seconds, each counted in UTC. The amount is reference
cost, native tokens, or both. Each part is a column of its own, so the gate
finds a scope's budgets by its key in the statement."""

from enum import StrEnum
from typing import ClassVar, Self

from pydantic import Field, model_validator

from acme.om.base import Identifiable, Platform, Trackable
from acme.om.budgets.types.amount import Amount

MAX_KEY = 200


class BudgetScopeKind(StrEnum):
    SESSION = "session"
    TREE = "tree"
    PERSON = "person"
    PROJECT = "project"
    TEAM = "team"
    TENANT = "tenant"


class BudgetScope(Platform):
    """One scope a call is charged to: its kind and the key the platform
    gives it, such as a session's id or a project's name."""

    kind: BudgetScopeKind
    key: str = Field(min_length=1, max_length=MAX_KEY)


class WindowKind(StrEnum):
    LIFE = "life"  # the scope's whole life: it never resets
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"  # from Monday
    MONTH = "month"
    SPAN = "span"  # a custom span of whole seconds, counted from the Unix epoch


class BudgetWindow(Platform):
    kind: WindowKind
    seconds: int | None = Field(default=None, ge=1)  # a span's length, and only a span's

    @model_validator(mode="after")
    def _a_span_has_a_length(self) -> Self:
        if (self.seconds is not None) != (self.kind is WindowKind.SPAN):
            raise ValueError("a span window has a length in seconds, and no other window has")
        return self


class Budget(Identifiable, Trackable):
    MANAGER_OWNED_FIELDS: ClassVar[tuple[str, ...]] = ("version",)
    """Every change of the amount is a compare-and-set on it."""

    scope_kind: BudgetScopeKind
    scope_key: str = Field(min_length=1, max_length=MAX_KEY)
    window_kind: WindowKind
    window_seconds: int | None = Field(default=None, ge=1)
    cost_micros: int | None = Field(default=None, ge=0)
    tokens: int | None = Field(default=None, ge=0)
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _a_window_and_an_amount(self) -> Self:
        # Each value object checks its own part.
        BudgetWindow(kind=self.window_kind, seconds=self.window_seconds)
        Amount(cost_micros=self.cost_micros, tokens=self.tokens)
        return self

    @property
    def scope(self) -> BudgetScope:
        return BudgetScope(kind=self.scope_kind, key=self.scope_key)

    @property
    def window(self) -> BudgetWindow:
        return BudgetWindow(kind=self.window_kind, seconds=self.window_seconds)

    @property
    def amount(self) -> Amount:
        return Amount(cost_micros=self.cost_micros, tokens=self.tokens)


class BudgetPage(Platform):
    items: tuple[Budget, ...]
    has_more: bool
