"""What each model call used and cost, kept apart from the history.

A step's shape carries its call's usage, but a memory-only session that
keeps no shape keeps no step at rest. A usage record holds no content at
all: ids, token counts, reference cost, a duration, and labels the product
defines, never a prompt, a reply, a tool's input or output, an attachment,
or a word a person typed. So it is written for every call a provider
billed, in every storage mode, once, and it outlives the history: a
session's purge leaves it, as it leaves the ledger. A call settled at its
whole hold is recorded at the hold, and marked (ADR 1014)."""

from uuid import UUID

from pydantic import Field

from acme.om.base import Created, Identifiable, Platform

MAX_LABEL = 200
"""The longest label a record keeps: an agent kind, a model role, a
provider, or a model, each a name the product or its catalog defines."""


class CallLabels(Platform):
    """What a record names of a call besides where it sat: its session, the
    session's tree, the agent kind and version that made it, its model role,
    and what served it."""

    session_id: UUID
    tree_id: UUID
    agent_kind: str = Field(min_length=1, max_length=MAX_LABEL)
    kind_version: int = Field(ge=1)
    role: str = Field(min_length=1, max_length=MAX_LABEL)
    provider: str = Field(min_length=1, max_length=MAX_LABEL)
    model: str = Field(min_length=1, max_length=MAX_LABEL)


class CallSite(Platform):
    """Where a billed call sat and how long its provider took to answer:
    what its caller knows of it that the gate does not. `step_id` is the
    call's response step. `labels` names a call the settling gate never
    held, a lost run's, read back from its request step and its session;
    such a call's latency is unknown, and is 0."""

    loop_id: UUID
    step_id: UUID
    latency_ms: int = Field(ge=0)
    labels: CallLabels | None = None


class UsageRecord(Identifiable, Created):
    """One model call a provider billed: the call's hold, where it sat (its
    session, the session's tree, its loop, its step), the agent kind and
    version that made it, what served it, its tokens in disjoint classes,
    its reference cost, and how long the provider took. Written once per
    hold. `step_id` is the call's response step, which a stale run may
    never land; the provider billed the call all the same.
    `settled_whole` marks a call whose usage was never reported whole: a
    broken stream, or a run lost before its reply. Its cost is its whole
    hold, as the ledger settled it, and its tokens are what a partial reply
    reported, else none."""

    hold_id: UUID
    session_id: UUID
    tree_id: UUID
    loop_id: UUID
    step_id: UUID
    agent_kind: str = Field(min_length=1, max_length=MAX_LABEL)
    kind_version: int = Field(ge=1)
    role: str = Field(min_length=1, max_length=MAX_LABEL)
    provider: str = Field(min_length=1, max_length=MAX_LABEL)
    model: str = Field(min_length=1, max_length=MAX_LABEL)
    input_tokens: int = Field(ge=0)
    cache_read_tokens: int = Field(ge=0)
    cache_write_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    thinking_tokens: int = Field(ge=0)
    cost_micros: int | None = Field(ge=0)  # None when no price applied
    latency_ms: int = Field(ge=0)
    settled_whole: bool = False


class UsageRollup(Platform):
    """The sum of some records: how many calls, their tokens by class, their
    reference cost, and their latency. `unpriced` counts the calls no price
    applied to; their cost is in no figure here, so a rollup with any is a
    floor, not a total. `settled_whole` counts the calls settled at their
    whole hold: their cost is in `cost_micros` at the hold, as the ledger
    counts it, and their tokens are partial at most."""

    calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    thinking_tokens: int = Field(default=0, ge=0)
    cost_micros: int = Field(default=0, ge=0)
    unpriced: int = Field(default=0, ge=0)
    settled_whole: int = Field(default=0, ge=0)
    latency_ms: int = Field(default=0, ge=0)


class LoopUsage(Platform):
    """One loop's rollup."""

    loop_id: UUID
    rollup: UsageRollup


class SessionUsage(Platform):
    """A page of a session's records, in the order they were written, a
    rollup per loop, in the order each loop first called, and the rollup of
    every record of the session. With `has_more`, the next page starts after
    the last record's id; `has_more_loops` says the session ran more loops
    than are rolled up here. Neither the loops nor the total depend on the
    page."""

    session_id: UUID
    records: tuple[UsageRecord, ...]
    has_more: bool
    loops: tuple[LoopUsage, ...]
    has_more_loops: bool
    total: UsageRollup
