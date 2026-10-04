"""Pure rules of a session: its status as a projection of its steps, and
what a new session takes from the session it came from. Values in, values
out; no clock, no storage.

The status moves on the steps alone:

- an input that wakes an idle session makes it pending, and a loop begins;
- a step a run writes makes a pending session running, and a `resumed`
  step makes any session running;
- a `parked` step parks it, and a `loop_ended` step makes it idle, or
  pending when a waking input is still undelivered and the loop reached
  an end of its own: a loop that ended `errored` or `cancelled` wakes on
  nothing it already held, so no loop restarts itself on the input that
  stopped it, or on the one a principal cancelled. That input waits, and
  the next request delivers it with whatever wakes the session next;
- a control that clears the park makes a parked session pending, for a
  run to take up: `unlock` and `cancel` clear any park, `resume` a pause,
  and `approve` or `deny` a person's;
- a principal's message clears a park on a question (`QUESTION`): it is
  the answer the park waits for, and the resumed loop delivers it.

An archived session records what arrives and wakes for nothing else. A
principal's message unarchives it, and wakes it as any input does. A
message to a parked session waits for the resume, unless the park waits
on a question; on any other park, what decides that a message is the
very thing the park waits for writes the control that clears it.

A waking input stays undelivered until a model request that references it
has a complete response: neither truncated nor abandoned. A request
delivers every pending input, so the latest one stands for them all.

The cache keeps attribution's two answers beside the status, folded over
the same steps (`attribution.rules.fold`): the speaker, which each model
request records, and the untrusted mark, which the first data sets for
good. Whether it holds private data is folded the same way: an input that
carries it from another session, a child's report, sets it for good."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from uuid import UUID

from acme.om.agent_sessions.types.agent_session import AgentSession, SessionStatus
from acme.om.attribution.rules import fold, principal_authored
from acme.om.steps.types.header import (
    ControlCommand,
    ControlHeader,
    InputHeader,
    LoopEndedHeader,
    LoopOutcome,
    MarkHeader,
    ModelRequestHeader,
    ModelResponseHeader,
    Park,
    ParkedHeader,
    ParkReason,
)
from acme.om.steps.types.step import Actor, Origin, Step, StepType

EVERY_PARK = frozenset(ParkReason)

UNPARKS: dict[ControlCommand, frozenset[ParkReason]] = {
    ControlCommand.UNLOCK: EVERY_PARK,
    ControlCommand.CANCEL: EVERY_PARK,
    ControlCommand.RESUME: frozenset({ParkReason.PAUSE}),
    ControlCommand.APPROVE: frozenset({ParkReason.PERSON}),
    ControlCommand.DENY: frozenset({ParkReason.PERSON}),
}
"""The parks each control clears. A control not named here clears none."""

QUESTION = Park(reason=ParkReason.PERSON, unlock="answer")
"""The park of a loop whose agent asked its person a question, or stood
down and said what it needs: a principal's message is the answer, and
clears it."""

RUN_ENDS = frozenset({StepType.PARKED, StepType.LOOP_ENDED})
"""The steps after which a run holds its session no longer."""

STOPPED = frozenset({LoopOutcome.ERRORED, LoopOutcome.CANCELLED})
"""The outcomes after which a loop wakes on nothing it already held: an
error no park can clear would meet the same input again, and a principal
who cancelled did not ask for another run."""


@dataclass(frozen=True)
class Projection:
    status: SessionStatus
    park: Park | None
    archived: bool
    pending_input: UUID | None = None  # the latest waking input not yet delivered
    delivering_request: UUID | None = None  # the request that carries it


def after_step(state: Projection, step: Step) -> Projection:
    """The projection once `step` is read. Each type holds the header its
    type fixes, so the header says which rule applies."""
    state = delivered(state, step)
    header = step.header
    if isinstance(header, InputHeader):
        # Only a principal's message unarchives a session or answers its
        # question: an agent's, such as a child's report, is data.
        archived = state.archived and not principal_authored(step)
        if not header.waking or archived:
            return replace(state, archived=archived)
        idle = state.status is SessionStatus.IDLE
        answered = state.park == QUESTION and principal_authored(step)
        status = SessionStatus.PENDING if idle or answered else state.status
        return replace(
            state,
            status=status,
            park=None if answered else state.park,
            archived=archived,
            pending_input=step.id,
            delivering_request=None,
        )
    if isinstance(header, ControlHeader):
        cleared = state.park is not None and state.park.reason in UNPARKS.get(
            header.command, frozenset()
        )
        if cleared:
            return replace(state, status=SessionStatus.PENDING, park=None)
        return state
    if isinstance(header, ParkedHeader):
        return replace(state, status=SessionStatus.PARKED, park=header.park)
    if isinstance(header, LoopEndedHeader):
        waiting = state.pending_input is not None and header.outcome not in STOPPED
        status = SessionStatus.PENDING if waiting else SessionStatus.IDLE
        pending = state.pending_input if waiting else None
        return replace(
            state, status=status, park=None, pending_input=pending, delivering_request=None
        )
    if step.type is StepType.RESUMED or state.status is SessionStatus.PENDING:
        return replace(state, status=SessionStatus.RUNNING, park=None)
    return state


def delivered(state: Projection, step: Step) -> Projection:
    """The undelivered input once `step` is read: a model request that
    references it carries it, and that request's complete response
    delivers it."""
    header = step.header
    if state.pending_input is None:
        return state
    if isinstance(header, ModelRequestHeader) and state.pending_input in step.refs:
        return replace(state, delivering_request=step.id)
    complete = (
        isinstance(header, ModelResponseHeader) and not header.truncated and not header.abandoned
    )
    if complete and step.responds_to == state.delivering_request:
        return replace(state, pending_input=None, delivering_request=None)
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
    state = Projection(
        session.status,
        session.park,
        session.archived_at is not None,
        session.pending_input,
        session.delivering_request,
    )
    for step in unread:
        state = after_step(state, step)
    speaker, untrusted = fold(session.speaker, session.untrusted, unread)
    archived_at = session.archived_at if state.archived else None
    holds_private = held_private(session.holds_private, unread)
    return AgentSession.model_validate(
        {
            **session.model_dump(),
            "status": state.status,
            "park": state.park,
            "pending_input": state.pending_input,
            "delivering_request": state.delivering_request,
            "archived_at": archived_at,
            "speaker": speaker,
            "untrusted": untrusted,
            "holds_private": holds_private,
            "status_seq": unread[-1].seq,
            "version": session.version + 1,
            "updated_at": now,
            "updated_by": by,
        }
    )


def held_private(held: bool, steps: Iterable[Step]) -> bool:
    """Whether a session holds private data once `steps` are read: it held
    it, or an input among them carries it from the session that wrote it,
    as a child's report does. Once set, it stays."""
    return held or any(
        isinstance(step.header, InputHeader) and step.header.holds_private for step in steps
    )


def announces(before: AgentSession, after: AgentSession, steps: Sequence[Step]) -> bool:
    """Whether a projection is announced: its status, its park, or its
    archive flag changed, or a loop ended among the steps it read. A step
    is never announced on its own."""
    return (
        (before.status, before.park, before.archived_at)
        != (after.status, after.park, after.archived_at)
    ) or any(step.type is StepType.LOOP_ENDED and step.seq > before.status_seq for step in steps)


def lineage(source: AgentSession | None, session: AgentSession) -> dict[str, Any]:
    """What a new session takes, whatever its maker sent. `source` is the
    session it came from, with its speaker and mark brought up to its
    history, or None for a root.

    A root starts on its own: depth 1, no speaker, unmarked. A session that
    came from another carries its mark, and holds private data where its
    source does, since its objective may carry it. A child also joins its
    parent's tree one level down and may call only the tools both its kind
    and its parent may: no child holds more than its parent. A session
    handed over roots a tree of its own. Whose authority it runs under, and who pays,
    is attribution's (`attribution.rules.inherited`)."""
    if source is None:
        return {"root_id": session.id, "depth": 1, "speaker": None, "untrusted": False}
    taken = {
        "speaker": None,
        "untrusted": source.untrusted,
        "holds_private": session.holds_private or source.holds_private,
    }
    if session.parent_id is None:
        return {**taken, "root_id": session.id, "depth": 1}
    return {
        **taken,
        "root_id": source.root_id,
        "depth": source.depth + 1,
        "tools": tuple(tool for tool in session.tools if tool in source.tools),
    }


def asks_for_run(before: AgentSession, after: AgentSession, steps: Sequence[Step]) -> bool:
    """Whether a projection asks for a run of the session's loop: it made the
    session pending, from idle, parked, or running, or it read a run's end
    among `steps`, a park or a loop's end, and left the session pending. An
    input that wakes it, an unlock or an answer that lets its park go, and a
    loop that ends with a waking input undelivered each turn it pending
    once, so each asks once. A session pending already has its run asked
    for, unless that run ended since, and a running one is held by the run
    that writes its steps. Read off the steps, as `wakes_at` is, so an
    answer that lands between a run's last step and its projection still
    asks."""
    if after.status is not SessionStatus.PENDING:
        return False
    if before.status is not SessionStatus.PENDING:
        return True
    return any(step.type in RUN_ENDS and step.seq > before.status_seq for step in steps)


def wakes_at(before: AgentSession, after: AgentSession, steps: Sequence[Step]) -> Park | None:
    """The park a projection asks to be woken from at its retry time: one a
    `parked` step among the steps it read set, which the session still waits
    on, when it carries a retry time. A park only a person clears carries
    none and asks for nothing. Read off the steps, not the status, so a loop
    that parks, wakes, and parks again within one read is still woken."""
    park = after.park
    if after.status is not SessionStatus.PARKED or park is None or park.retry_at is None:
        return None
    if any(step.type is StepType.PARKED and step.seq > before.status_seq for step in steps):
        return park
    return None


def parked_step(step_id: UUID, session_id: UUID, loop_id: UUID, park: Park, now: datetime) -> Step:
    """The step a run writes when its loop parks: no outcome, only the park."""
    return Step(
        id=step_id,
        created_at=now,
        session_id=session_id,
        loop_id=loop_id,
        type=StepType.PARKED,
        actor=Actor.ENGINE,
        origin=Origin.ENGINE,
        header=ParkedHeader(park=park),
    )


def resumed_step(step_id: UUID, session_id: UUID, loop_id: UUID, now: datetime) -> Step:
    """The step that starts a new run of a loop whose unlock happened."""
    return Step(
        id=step_id,
        created_at=now,
        session_id=session_id,
        loop_id=loop_id,
        type=StepType.RESUMED,
        actor=Actor.ENGINE,
        origin=Origin.ENGINE,
        header=MarkHeader(),
    )


def unlock_step(step_id: UUID, session_id: UUID, now: datetime) -> Step:
    """The control the engine writes when a park's unlock happens by itself:
    its retry time came, or the reason it waited on is gone. It arrives
    through the inbox, as every control does, outside any run, so its loop id
    is its own."""
    return Step(
        id=step_id,
        created_at=now,
        session_id=session_id,
        loop_id=step_id,
        type=StepType.CONTROL,
        actor=Actor.ENGINE,
        origin=Origin.ENGINE,
        header=ControlHeader(command=ControlCommand.UNLOCK),
    )


def _seq(step: Step) -> int:
    return step.seq


def purge_due(session: AgentSession, deleted_before: datetime) -> bool:
    """Whether a session's shape is past its retention: it was marked deleted
    before the cut. The retention counts from the mark, and nothing else
    starts a purge, so a session that was never marked, or was marked
    since the cut, is never purged one session at a time."""
    return session.deleted_at is not None and session.deleted_at < deleted_before
