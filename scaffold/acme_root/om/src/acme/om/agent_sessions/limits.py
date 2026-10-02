"""Pure rules of a loop's limits of time, steps, and streaks: what each one
measures, and what it does when it trips. Values in, values out; no clock,
no storage. The budget, a guard too, trips at the gate
(`budgets.rules.budget_park`).

| Limit | Measures | Kind | When it trips |
|---|---|---|---|
| Deadline | one instant the tree shares | guard | parks for a person |
| Step guard | model calls in one loop | guard | parks so a person looks |
| Error streak | consecutive tool errors, or identical calls | bound | ends `inconclusive` |
| Nudges | turns that neither continue nor submit | bound | ends `inconclusive` |
| Run time | wall time of one run | yield | hands the loop back to the queue |

A guard parks, a bound ends the loop, a yield hands it to a new run. A bound
is a loop that cannot make progress, which no raised limit cures, so it is
read first: a loop that trips a bound and a guard at once ends. Its outcome
is `inconclusive`, never `failed`, and it never ends the session.

The step guard is on always. It is the one backstop a free model or a
missing price cannot switch off: `Limits` refuses a guard of zero or past
its ceiling, and `tripped` holds a value that went around the check to the
same bounds. Every loop starts it afresh, and a person's look after it
parks starts it again, so it is never a lifetime cap."""

from collections.abc import Iterable
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from acme.om.base import Platform
from acme.om.steps.types.header import (
    LoopOutcome,
    ModelResponseHeader,
    Park,
    ParkedHeader,
    ParkReason,
    ToolRequestHeader,
)
from acme.om.steps.types.step import Step, StepType

STEP_GUARD_CEILING = 1000
"""The most model calls a step guard lets one loop make between two looks
of a person. A larger guard is no guard."""

STEP_GUARD_UNLOCK = "step_guard"
"""The step guard's park: a person looks, and an unlock lets the loop go on."""

DEADLINE_UNLOCK = "deadline"
"""The deadline's park: a person extends the tree's deadline."""


class Limit(StrEnum):
    BUDGET = "budget"
    DEADLINE = "deadline"
    STEP_GUARD = "step_guard"
    ERROR_STREAK = "error_streak"
    NUDGES = "nudges"
    RUN_TIME = "run_time"


class LimitKind(StrEnum):
    GUARD = "guard"  # parks the loop
    BOUND = "bound"  # ends the loop `inconclusive`
    YIELD = "yield"  # hands the loop to a new run


LIMIT_KINDS: dict[Limit, LimitKind] = {
    Limit.BUDGET: LimitKind.GUARD,
    Limit.DEADLINE: LimitKind.GUARD,
    Limit.STEP_GUARD: LimitKind.GUARD,
    Limit.ERROR_STREAK: LimitKind.BOUND,
    Limit.NUDGES: LimitKind.BOUND,
    Limit.RUN_TIME: LimitKind.YIELD,
}


class Limits(Platform):
    """The limits of one loop. Each has a value; none can be switched off."""

    step_guard: int = Field(default=50, ge=1, le=STEP_GUARD_CEILING)
    error_streak: int = Field(default=5, ge=1)  # consecutive tool errors, or identical calls
    nudges: int = Field(default=3, ge=0)  # nudges one loop gives before it ends
    run_time: timedelta = Field(default=timedelta(minutes=15), gt=timedelta(0))


class LoopTally(Platform):
    """What a loop's limits read, folded from its steps in `seq` order."""

    model_calls: int = 0  # since the loop began, or since its step guard last parked
    tool_errors: int = 0  # consecutive tool responses that failed
    repeats: int = 0  # the run of identical tool calls the latest one ends
    nudges: int = 0  # consecutive model turns that called no tool
    last_call: tuple[str, str] | None = None  # the latest tool call's name and input hash


class Trip(Platform):
    """A limit that tripped, and what it does: a guard's park, a bound's
    outcome, or neither for a yield."""

    limit: Limit
    park: Park | None = None
    outcome: LoopOutcome | None = None

    @model_validator(mode="after")
    def _as_its_kind_says(self) -> Self:
        kind = LIMIT_KINDS[self.limit]
        if (self.park is not None) != (kind is LimitKind.GUARD):
            raise ValueError(f"a {kind.value} parks if and only if it is a guard")
        if (self.outcome is not None) != (kind is LimitKind.BOUND):
            raise ValueError(f"a {kind.value} ends the loop if and only if it is a bound")
        if self.outcome is not None and self.outcome is not LoopOutcome.INCONCLUSIVE:
            raise ValueError("a bound ends the loop inconclusive, never with a conclusion")
        return self

    @property
    def kind(self) -> LimitKind:
        return LIMIT_KINDS[self.limit]


def step_guard_park() -> Park:
    return Park(reason=ParkReason.PERSON, unlock=STEP_GUARD_UNLOCK)


def deadline_park() -> Park:
    return Park(reason=ParkReason.PERSON, unlock=DEADLINE_UNLOCK)


def tallied(tally: LoopTally, step: Step) -> LoopTally:
    """The tally once `step` is read. A truncated or abandoned response is
    continued or retried, so it is no turn of its own."""
    header = step.header
    if step.type is StepType.MODEL_REQUEST:
        return tally.model_copy(update={"model_calls": tally.model_calls + 1})
    if isinstance(header, ModelResponseHeader):
        if header.truncated or header.abandoned:
            return tally
        called = any(block.kind == "tool_use" for block in step.content.blocks)
        return tally.model_copy(update={"nudges": 0 if called else tally.nudges + 1})
    if isinstance(header, ToolRequestHeader):
        call = (header.tool, header.input_hash)
        repeats = tally.repeats + 1 if call == tally.last_call else 1
        return tally.model_copy(update={"repeats": repeats, "last_call": call})
    if step.type is StepType.TOOL_RESPONSE:
        failed = step.as_tool_response().is_error
        return tally.model_copy(update={"tool_errors": tally.tool_errors + 1 if failed else 0})
    if isinstance(header, ParkedHeader) and header.park == step_guard_park():
        return tally.model_copy(update={"model_calls": 0})
    return tally


def tally_loop(steps: Iterable[Step], loop_id: UUID) -> LoopTally:
    """The tally of one loop: its own steps alone, in `seq` order, so every
    loop starts each count afresh."""
    tally = LoopTally()
    for step in sorted((s for s in steps if s.loop_id == loop_id), key=lambda s: s.seq):
        tally = tallied(tally, step)
    return tally


def tripped(
    limits: Limits,
    tally: LoopTally,
    *,
    now: datetime,
    run_started_at: datetime,
    deadline: datetime | None,
) -> Trip | None:
    """The limit the loop trips before its next model call, or None. The
    bounds come first, then the guards, then the yield. `deadline` is the
    tree's, the session's own, never the context's: a worker's stage carries
    none."""
    if tally.tool_errors >= limits.error_streak or tally.repeats >= limits.error_streak:
        return Trip(limit=Limit.ERROR_STREAK, outcome=LoopOutcome.INCONCLUSIVE)
    if tally.nudges > limits.nudges:
        return Trip(limit=Limit.NUDGES, outcome=LoopOutcome.INCONCLUSIVE)
    if deadline is not None and now >= deadline:
        return Trip(limit=Limit.DEADLINE, park=deadline_park())
    # Held to its bounds here as well, so a value copied around the check
    # neither switches the guard off nor makes it unbounded.
    guard = min(max(limits.step_guard, 1), STEP_GUARD_CEILING)
    if tally.model_calls >= guard:
        return Trip(limit=Limit.STEP_GUARD, park=step_guard_park())
    if now - run_started_at >= limits.run_time:
        return Trip(limit=Limit.RUN_TIME)
    return None


def cut_timeout(timeout: timedelta, now: datetime, deadline: datetime | None) -> timedelta:
    """A tool's timeout, cut to the tree's deadline: never past it."""
    if deadline is None:
        return timeout
    return max(timedelta(0), min(timeout, deadline - now))
