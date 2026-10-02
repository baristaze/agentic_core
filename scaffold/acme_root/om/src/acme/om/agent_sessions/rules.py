"""Pure rules of a session: its status as a projection of its steps, and
what a new session takes from the session it came from. Values in, values
out; no clock, no storage.

The status moves on the steps alone:

- an input that wakes an idle session makes it pending, and a loop begins;
- a step a run writes makes a pending session running, and a `resumed`
  step makes any session running;
- a `parked` step parks it, and a `loop_ended` step makes it idle, or
  pending when a waking input is still undelivered;
- a control that clears the park makes a parked session pending, for a
  run to take up: `unlock` and `cancel` clear any park, `resume` a pause,
  and `approve` or `deny` a person's.

An archived session records what arrives and wakes for nothing else. A
principal's message unarchives it, and wakes it as any input does. A
message to a parked session waits for the resume; what decides that a
message is the very thing the park waits for writes the control that
clears it.

A waking input stays undelivered until a model request that references it
has a complete response: neither truncated nor abandoned. A request
delivers every pending input, so the latest one stands for them all.

The cache keeps attribution's two answers beside the status, folded over
the same steps (`attribution.rules.fold`): the speaker, which a principal's
message moves, and the untrusted mark, which the first data sets for
good."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from uuid import UUID

from acme.om.agent_sessions.types.agent_session import AgentSession, SessionStatus
from acme.om.attribution.rules import call_principal, fold, spender_of
from acme.om.attribution.types.authority import Authority
from acme.om.steps.types.header import (
    ControlCommand,
    ControlHeader,
    InputHeader,
    ModelRequestHeader,
    ModelResponseHeader,
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
    pending_input: UUID | None = None  # the latest waking input not yet delivered
    delivering_request: UUID | None = None  # the request that carries it


def after_step(state: Projection, step: Step) -> Projection:
    """The projection once `step` is read. Each type holds the header its
    type fixes, so the header says which rule applies."""
    state = delivered(state, step)
    header = step.header
    if isinstance(header, InputHeader):
        archived = state.archived and step.type is not StepType.MESSAGE
        if not header.waking or archived:
            return replace(state, archived=archived)
        idle = state.status is SessionStatus.IDLE
        status = SessionStatus.PENDING if idle else state.status
        return replace(
            state,
            status=status,
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
    if step.type is StepType.LOOP_ENDED:
        waiting = state.pending_input is not None
        status = SessionStatus.PENDING if waiting else SessionStatus.IDLE
        return replace(state, status=status, park=None, delivering_request=None)
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


def lineage(source: AgentSession | None, session: AgentSession) -> dict[str, Any]:
    """What a new session takes, whatever its maker sent. `source` is the
    session it came from, with its speaker and mark brought up to its
    history, or None for a root.

    A root starts on its own: depth 1, no spender, no speaker, unmarked. A
    session that came from another takes its mark, and runs under the
    principal that session's own calls run under, never one its maker
    names. A child also joins its parent's tree one level down, pays as its
    parent pays, and may call only the tools both its kind and its parent
    may: no child holds more than its parent. A session handed over roots a
    tree of its own, and pays as the principal who confirms its work."""
    if source is None:
        return {
            "root_id": session.id,
            "depth": 1,
            "spender": None,
            "speaker": None,
            "untrusted": False,
        }
    principal = call_principal(source.authority, source.speaker)
    taken = {
        "speaker": None,
        "untrusted": source.untrusted,
        "authority": Authority(mode=session.authority.mode, principal=principal),
    }
    if session.parent_id is None:
        return {**taken, "root_id": session.id, "depth": 1, "spender": None}
    return {
        **taken,
        "root_id": source.root_id,
        "depth": source.depth + 1,
        "spender": spender_of(source.spender, source.speaker),
        "tools": tuple(tool for tool in session.tools if tool in source.tools),
    }


def _seq(step: Step) -> int:
    return step.seq
