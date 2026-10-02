"""A loop's limits of time, steps, and streaks, each tripping as its kind
says: a guard parks, a bound ends the loop `inconclusive`, a yield hands it
to a new run. And the step guard, which nothing switches off."""

from collections.abc import Callable
from datetime import timedelta
from uuid import UUID

import pytest
from contracts.step_storage import make_message, make_request
from pydantic import ValidationError

from acme.om.agent_sessions.limits import (
    DEADLINE_UNLOCK,
    LIMIT_KINDS,
    STEP_GUARD_CEILING,
    STEP_GUARD_UNLOCK,
    Limit,
    LimitKind,
    Limits,
    LoopTally,
    Trip,
    cut_timeout,
    step_guard_park,
    tally_loop,
    tripped,
)
from acme.om.agent_sessions.rules import parked_step
from acme.om.base import new_id, utcnow
from acme.om.budgets.rules import budget_park
from acme.om.budgets.types.amount import AmountUnit
from acme.om.budgets.types.breach import Breach, BreachAction, Refusal
from acme.om.steps.types.content import Content, TextBlock, ToolResultBlock, ToolUseBlock
from acme.om.steps.types.header import (
    LoopOutcome,
    ModelResponseHeader,
    Park,
    ParkReason,
    ToolRequestHeader,
    ToolResponseHeader,
)
from acme.om.steps.types.step import Actor, Origin, Step, StepType

SESSION = new_id()
NOW = utcnow()
LIMITS = Limits(step_guard=3, error_streak=2, nudges=1, run_time=timedelta(minutes=10))


def check(
    tally: LoopTally,
    *,
    limits: Limits = LIMITS,
    ran: timedelta = timedelta(0),
    deadline_in: timedelta | None = None,
) -> Trip | None:
    deadline = None if deadline_in is None else NOW + deadline_in
    return tripped(limits, tally, now=NOW, run_started_at=NOW - ran, deadline=deadline)


def refusal() -> Refusal:
    return Refusal(
        breaches=(
            Breach(
                budget_id=new_id(),
                scope=None,
                unit=AmountUnit.COST,
                amount=1,
                committed=1,
                held=0,
                exposure=1,
                action=BreachAction.RAISE,
                needed=2,
                resets_at=NOW + timedelta(hours=3),
            ),
        )
    )


TRIPS: dict[Limit, Callable[[], Trip | None]] = {
    Limit.BUDGET: lambda: Trip(limit=Limit.BUDGET, park=budget_park(refusal(), NOW)),
    Limit.DEADLINE: lambda: check(LoopTally(), deadline_in=timedelta(0)),
    Limit.STEP_GUARD: lambda: check(LoopTally(model_calls=3)),
    Limit.ERROR_STREAK: lambda: check(LoopTally(tool_errors=2)),
    Limit.NUDGES: lambda: check(LoopTally(nudges=2)),
    Limit.RUN_TIME: lambda: check(LoopTally(), ran=timedelta(minutes=10)),
}
"""One way to trip each limit."""


@pytest.mark.parametrize("limit", list(Limit))
def test_each_limit_trips_as_its_kind_says(limit: Limit) -> None:
    trip = TRIPS[limit]()
    assert trip is not None and trip.limit is limit
    kind = LIMIT_KINDS[limit]
    if kind is LimitKind.GUARD:
        assert trip.park is not None and trip.outcome is None, "a guard parks"
    elif kind is LimitKind.BOUND:
        assert (trip.park, trip.outcome) == (None, LoopOutcome.INCONCLUSIVE), "a bound ends"
    else:
        assert (trip.park, trip.outcome) == (None, None), "a yield hands the loop on"


def test_the_table_of_kinds() -> None:
    assert {limit for limit, kind in LIMIT_KINDS.items() if kind is LimitKind.GUARD} == {
        Limit.BUDGET,
        Limit.DEADLINE,
        Limit.STEP_GUARD,
    }
    assert {limit for limit, kind in LIMIT_KINDS.items() if kind is LimitKind.BOUND} == {
        Limit.ERROR_STREAK,
        Limit.NUDGES,
    }
    assert set(LIMIT_KINDS) == set(Limit)


def test_a_trip_that_breaks_its_kind_is_refused() -> None:
    with pytest.raises(ValidationError, match="parks if and only if"):
        Trip(limit=Limit.ERROR_STREAK, park=step_guard_park(), outcome=LoopOutcome.INCONCLUSIVE)
    with pytest.raises(ValidationError, match="parks if and only if"):
        Trip(limit=Limit.STEP_GUARD)
    with pytest.raises(ValidationError, match="never with a conclusion"):
        Trip(limit=Limit.NUDGES, outcome=LoopOutcome.FAILED)
    with pytest.raises(ValidationError, match="ends the loop if and only if"):
        Trip(limit=Limit.RUN_TIME, outcome=LoopOutcome.INCONCLUSIVE)


def test_the_parks_name_their_unlock_and_only_a_person_clears_them() -> None:
    deadline = check(LoopTally(), deadline_in=-timedelta(seconds=1))
    guard = check(LoopTally(model_calls=3))
    assert deadline is not None and deadline.park is not None
    assert guard is not None and guard.park is not None
    assert (deadline.park.reason, deadline.park.unlock) == (ParkReason.PERSON, DEADLINE_UNLOCK)
    assert (guard.park.reason, guard.park.unlock) == (ParkReason.PERSON, STEP_GUARD_UNLOCK)
    assert deadline.park.retry_at is None and guard.park.retry_at is None
    with pytest.raises(ValidationError, match="never by a time"):
        Park(reason=ParkReason.PERSON, unlock=STEP_GUARD_UNLOCK, retry_at=NOW)


def test_within_its_limits_a_loop_goes_on() -> None:
    tally = LoopTally(model_calls=2, tool_errors=1, repeats=1, nudges=1)
    assert check(tally, ran=timedelta(minutes=9), deadline_in=timedelta(minutes=1)) is None


def test_a_bound_ends_the_loop_even_when_a_guard_trips_too() -> None:
    """A loop that cannot make progress is not cured by a person's look."""
    both = check(LoopTally(model_calls=3, tool_errors=2), deadline_in=timedelta(0))
    assert both is not None and both.limit is Limit.ERROR_STREAK


# The step guard.


@pytest.mark.parametrize("value", [0, -1, None, STEP_GUARD_CEILING + 1])
def test_no_setting_switches_the_step_guard_off(value: int | None) -> None:
    with pytest.raises(ValidationError):
        Limits.model_validate({"step_guard": value})


@pytest.mark.parametrize(("value", "trips_at"), [(0, 1), (-5, 1), (10**9, STEP_GUARD_CEILING)])
def test_a_guard_copied_around_the_check_is_held_to_it(value: int, trips_at: int) -> None:
    """A copy is not validated, so the check holds the same bounds itself."""
    copied = Limits().model_copy(update={"step_guard": value})
    assert check(LoopTally(model_calls=trips_at - 1), limits=copied) is None
    trip = check(LoopTally(model_calls=trips_at), limits=copied)
    assert trip is not None and trip.limit is Limit.STEP_GUARD


def test_the_step_guard_is_on_by_default() -> None:
    assert 1 <= Limits().step_guard <= STEP_GUARD_CEILING


EMPTY = Content()


def a_step(loop_id: UUID, step_type: StepType, header: object, content: Content = EMPTY) -> Step:
    return Step.model_validate(
        {
            "id": new_id(),
            "created_at": NOW,
            "session_id": SESSION,
            "loop_id": loop_id,
            "type": step_type,
            "actor": Actor.ENGINE,
            "origin": Origin.ENGINE,
            "responds_to": new_id() if step_type.is_response() else None,
            "refs": (new_id(),) if step_type is StepType.TOOL_REQUEST else (),
            "header": header,
            "content": content,
        }
    )


def numbered(steps: list[Step]) -> list[Step]:
    return [step.model_copy(update={"seq": n}) for n, step in enumerate(steps, start=1)]


def test_every_loop_starts_the_step_guard_afresh() -> None:
    first, second = new_id(), new_id()
    history = numbered(
        [make_request(SESSION, first) for _ in range(5)] + [make_request(SESSION, second)]
    )
    assert tally_loop(history, first).model_calls == 5
    assert tally_loop(history, second).model_calls == 1


def test_a_persons_look_restarts_the_step_guard_and_no_other_park_does() -> None:
    loop = new_id()
    budget = Park(reason=ParkReason.BUDGET, unlock="b", retry_at=NOW)
    history = numbered(
        [make_request(SESSION, loop) for _ in range(3)]
        + [parked_step(new_id(), SESSION, loop, step_guard_park(), NOW)]
        + [make_request(SESSION, loop)]
        + [parked_step(new_id(), SESSION, loop, budget, NOW)]
        + [make_request(SESSION, loop)]
    )
    assert tally_loop(history, loop).model_calls == 2


# The streaks.


def response(loop: UUID, *, calls: bool, truncated: bool = False) -> Step:
    blocks = (ToolUseBlock(id="c", name="read_log"),) if calls else (TextBlock(text="done?"),)
    header = ModelResponseHeader(truncated=truncated)
    return a_step(loop, StepType.MODEL_RESPONSE, header, Content(blocks=blocks))


def tool_request(loop: UUID, input_hash: str) -> Step:
    header = ToolRequestHeader(tool="read_log", tool_use_id="c", input_hash=input_hash)
    return a_step(loop, StepType.TOOL_REQUEST, header)


def tool_response(loop: UUID, *, failed: bool) -> Step:
    result = ToolResultBlock(tool_use_id="c", is_error=failed)
    return a_step(loop, StepType.TOOL_RESPONSE, ToolResponseHeader(), Content(blocks=(result,)))


def test_consecutive_tool_errors_end_the_loop_and_a_success_resets_them() -> None:
    loop = new_id()
    errors = [tool_response(loop, failed=True), tool_response(loop, failed=True)]
    assert tally_loop(numbered(errors), loop).tool_errors == 2
    broken = tally_loop(numbered([errors[0], tool_response(loop, failed=False), errors[1]]), loop)
    assert broken.tool_errors == 1
    assert check(broken) is None


def test_identical_calls_in_a_row_end_the_loop() -> None:
    loop = new_id()
    same = tally_loop(numbered([tool_request(loop, "h:1"), tool_request(loop, "h:1")]), loop)
    assert same.repeats == 2
    trip = check(same)
    assert trip is not None and trip.limit is Limit.ERROR_STREAK
    varied = tally_loop(numbered([tool_request(loop, "h:1"), tool_request(loop, "h:2")]), loop)
    assert varied.repeats == 1


def test_turns_that_call_no_tool_earn_nudges_until_they_end_the_loop() -> None:
    loop = new_id()
    idle = [response(loop, calls=False), response(loop, calls=False)]
    assert tally_loop(numbered(idle), loop).nudges == 2
    trip = check(tally_loop(numbered(idle), loop))
    assert trip is not None and trip.limit is Limit.NUDGES
    # A turn that calls a tool resets them; a cut-off turn is continued, not nudged.
    progress = [idle[0], response(loop, calls=True), response(loop, calls=False, truncated=True)]
    assert tally_loop(numbered(progress), loop).nudges == 0
    assert tally_loop(numbered([make_message(SESSION)]), loop) == LoopTally()


# The deadline.


def test_no_call_starts_after_the_deadline_and_a_tools_timeout_is_cut_to_it() -> None:
    assert check(LoopTally(), deadline_in=timedelta(seconds=1)) is None
    trip = check(LoopTally(), deadline_in=timedelta(0))
    assert trip is not None and trip.limit is Limit.DEADLINE
    assert cut_timeout(timedelta(minutes=5), NOW, NOW + timedelta(seconds=30)) == timedelta(
        seconds=30
    )
    assert cut_timeout(timedelta(seconds=5), NOW, NOW + timedelta(seconds=30)) == timedelta(
        seconds=5
    )
    assert cut_timeout(timedelta(seconds=5), NOW, NOW - timedelta(seconds=1)) == timedelta(0)
    assert cut_timeout(timedelta(seconds=5), NOW, None) == timedelta(seconds=5)
