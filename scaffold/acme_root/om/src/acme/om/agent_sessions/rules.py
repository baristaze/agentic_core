"""Pure rules of a session: its status as a projection of its steps. Values
in, values out; no clock, no storage.

The status moves on the steps alone:

- an input that wakes an idle session makes it pending, and a loop begins;
- a step a run writes makes a pending session running, and a `resumed`
  step makes any session running;
- a `parked` step parks it, and a `loop_ended` step makes it idle;
- a control that clears the park makes a parked session pending, for a
  run to take up: `unlock` and `cancel` clear any park, `resume` a pause,
  and `approve` or `deny` a person's.

An archived session records what arrives and wakes for nothing else. A
principal's message unarchives it, and wakes it as any input does. A
message to a parked session waits for the resume; what decides that a
message is the very thing the park waits for writes the control that
clears it."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from acme.om.agent_sessions.types.agent_session import AgentSession, SessionStatus
from acme.om.steps.types.header import (
    ControlCommand,
    ControlHeader,
    InputHeader,
    Park,
    ParkedHeader,
    ParkReason,
)
from acme.om.steps.types.step import Step, StepType

EVERY_PARK = frozenset(ParkReason)

UNPARKS: dict[ControlCommand, frozenset[ParkReason]] = {
    ControlCommand.UNLOCK: EVERY_PARK,
    ControlCommand.CANCEL: EVERY_PARK,
    ControlCommand.RESUME: frozenset({ParkReason.PAUSE}),
    ControlCommand.APPROVE: frozenset({ParkReason.PERSON}),
    ControlCommand.DENY: frozenset({ParkReason.PERSON}),
}
"""The parks each control clears. A control not named here clears none."""


@dataclass(frozen=True)
class Projection:
    status: SessionStatus
    park: Park | None
    archived: bool


def after_step(state: Projection, step: Step) -> Projection:
    """The projection once `step` is read. Each type holds the header its
    type fixes, so the header says which rule applies."""
    header = step.header
    if isinstance(header, InputHeader):
        archived = state.archived and step.type is not StepType.MESSAGE
        wakes = header.waking and not archived and state.status is SessionStatus.IDLE
        status = SessionStatus.PENDING if wakes else state.status
        return Projection(status, state.park, archived)
    if isinstance(header, ControlHeader):
        cleared = state.park is not None and state.park.reason in UNPARKS.get(
            header.command, frozenset()
        )
        if cleared:
            return Projection(SessionStatus.PENDING, None, state.archived)
        return state
    if isinstance(header, ParkedHeader):
        return Projection(SessionStatus.PARKED, header.park, state.archived)
    if step.type is StepType.LOOP_ENDED:
        return Projection(SessionStatus.IDLE, None, state.archived)
    if step.type is StepType.RESUMED or state.status is SessionStatus.PENDING:
        return Projection(SessionStatus.RUNNING, None, state.archived)
    return state


def projected(
    session: AgentSession, steps: Sequence[Step], now: datetime, by: UUID
) -> AgentSession:
    """The session with its cached status brought up to the steps after
    `status_seq`, read in `seq` order; steps at or below it are read
    already and change nothing. The copy takes the next version. With no
    step to read, the session is answered as it is."""
    unread = sorted((step for step in steps if step.seq > session.status_seq), key=_seq)
    if not unread:
        return session
    state = Projection(session.status, session.park, session.archived_at is not None)
    for step in unread:
        state = after_step(state, step)
    archived_at = session.archived_at if state.archived else None
    return AgentSession.model_validate(
        {
            **session.model_dump(),
            "status": state.status,
            "park": state.park,
            "archived_at": archived_at,
            "status_seq": unread[-1].seq,
            "version": session.version + 1,
            "updated_at": now,
            "updated_by": by,
        }
    )


def announces(before: AgentSession, after: AgentSession, steps: Sequence[Step]) -> bool:
    """Whether a projection is announced: its status, its park, or its
    archive flag changed, or a loop ended among the steps it read. A step
    is never announced on its own."""
    return (
        (before.status, before.park, before.archived_at)
        != (after.status, after.park, after.archived_at)
    ) or any(step.type is StepType.LOOP_ENDED and step.seq > before.status_seq for step in steps)


def _seq(step: Step) -> int:
    return step.seq
