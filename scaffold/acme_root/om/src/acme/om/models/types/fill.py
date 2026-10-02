"""Model roles, fills, and fill sets. A call site names a model role, never
a model; a fill serves it; a session keeps its fills as a versioned fill
set, and a switch is a new version, announced.

In code a model role is `ModelRole`, never the tenancy namespace's `Role`."""

from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from acme.integrations.model_providers.types import Effort, ProviderName
from acme.om.base import Created, Identifiable, Platform

ModelRole = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
"""A task a call site names: the main agent, the summarizer, a title, a log
triage. The set is open; an agent kind names the ones it calls."""

MAIN: ModelRole = "main"
SUMMARIZER: ModelRole = "summarizer"
"""The two model roles the engine itself calls: the agent's own turns, and
the compaction that folds a window into a summary."""


class OutputShape(StrEnum):
    TEXT = "text"
    SCHEMA = "schema"  # an answer in the shape a named schema gives


class Eligibility(Platform):
    """What a fill offers, or what a session requires of every fill it may
    run on: zero data retention, a region."""

    zero_retention: bool = False
    region: str | None = Field(default=None, min_length=1, max_length=64)

    def admits(self, offered: Eligibility) -> bool:
        """Whether a fill that offers `offered` meets this requirement."""
        if self.zero_retention and not offered.zero_retention:
            return False
        return self.region is None or offered.region == self.region


class Fill(Platform):
    """What serves a model role: a provider, a model, how hard it works, its
    output bound and shape, its context window, and the eligibility it
    carries."""

    provider: ProviderName
    model: str = Field(min_length=1, max_length=200)
    effort: Effort | None = None
    thinking_budget: int | None = Field(default=None, gt=0)
    max_output_tokens: int = Field(gt=0)
    output: OutputShape = OutputShape.TEXT
    schema_name: str | None = Field(default=None, min_length=1, max_length=200)
    context_window: int = Field(gt=0)
    eligibility: Eligibility = Eligibility()

    @model_validator(mode="after")
    def _a_schema_is_named(self) -> Self:
        if (self.output is OutputShape.SCHEMA) != (self.schema_name is not None):
            raise ValueError("a fill names a schema exactly when its output is one")
        if self.max_output_tokens >= self.context_window:
            raise ValueError("a fill's output bound fits inside its window")
        return self

    @property
    def name(self) -> str:
        return f"{self.provider.value}/{self.model}"


class RoleFill(Platform):
    """One model role's fill in a fill set, and the fallbacks it declares, in
    the order a fallback takes them."""

    role: ModelRole
    fill: Fill
    fallbacks: tuple[Fill, ...] = ()


class SwitchReason(StrEnum):
    """Why a fill set took a new version."""

    FALLBACK = "fallback"  # the provider failed and a declared fallback took over
    RETIRED = "retired"  # the model is gone, and the role was resolved again
    UPGRADE = "upgrade"  # a better model was published
    POLICY = "policy"  # a policy said so


class FillSwitch(Platform):
    """What a `switched` step says of a fill switch: the role, both fills,
    the version the switch made, and why."""

    role: ModelRole
    from_fill: Fill
    to_fill: Fill
    fill_set_version: int = Field(ge=2)
    reason: SwitchReason

    @model_validator(mode="after")
    def _a_switch_changes_the_fill(self) -> Self:
        if self.from_fill == self.to_fill:
            raise ValueError("a switch names two different fills")
        return self


class FillSet(Identifiable, Created):
    """A session's resolution of its model roles to fills, at one version.
    Each version is written once; a switch writes the next one. The first
    is the session's resolution and names no switch; every later one names
    why, and the `switched` step that announced it."""

    session_id: UUID
    version: int = Field(ge=1)
    roles: tuple[RoleFill, ...]
    eligibility: Eligibility = Eligibility()  # what the session requires of every fill
    reason: SwitchReason | None = None
    switched_by: UUID | None = None  # the `switched` step

    @model_validator(mode="after")
    def _its_shape(self) -> Self:
        names = [r.role for r in self.roles]
        if len(set(names)) != len(names):
            raise ValueError("a fill set names a model role once")
        if names != sorted(names):
            raise ValueError("a fill set holds its roles in order")
        first = self.version == 1
        if first != (self.reason is None) or first != (self.switched_by is None):
            raise ValueError("the first version names no switch, and every later one names its own")
        return self

    def role_fill(self, role: ModelRole) -> RoleFill | None:
        return next((r for r in self.roles if r.role == role), None)

    def fill_for(self, role: ModelRole) -> Fill | None:
        found = self.role_fill(role)
        return None if found is None else found.fill
