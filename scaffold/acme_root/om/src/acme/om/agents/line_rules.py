"""Pure rules of a session in line for a leased resource: the ask a tool's
call makes, the asks a session holds, what each one's standing has not yet
told the model, and the park of a loop that waits on them. Values in,
values out; no clock, no storage.

A session waits in line as a waiter of the leases namespace. Its tool asks
and answers at once with its place; the loop parks only when the turn ends
with nothing else to do. A grant while its lease lives, a request's end
without a lease, and the lease's end each reach the model as the engine's
notice, written by a run before its next model call, in the loop that
asked or a later one. Each notice's id is derived from the call's answer
and what it tells, so the history says what the model was told, after a
lost run as well (ADR 1024).

A job's tool may ask in line too, for the resource its work runs on. Its
call stays open: the loop parks in line at once, the grant starts the job
in its own commit, and the park moves to the job's, so the job's result
answers the call with no notice and no model call between (ADR 1026)."""

import json
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import ValidationError

from acme.om.agents.types.line import InLine
from acme.om.base import derived_id
from acme.om.leases.types.lease import LeaseStatus
from acme.om.leases.types.request import (
    EndReason,
    LeaseRequest,
    RequestStatus,
    Standing,
    WaiterKind,
)
from acme.om.steps.types.content import TextBlock
from acme.om.steps.types.header import (
    InputHeader,
    JobPark,
    LinePark,
    Park,
    ParkReason,
    ToolFailure,
    ToolRequestHeader,
    ToolResponseHeader,
)
from acme.om.steps.types.step import Actor, Step

LINE_UNLOCK = "grant"
"""What clears a park in line: the grant, or its request's end without one."""


class Answer(StrEnum):
    """What the model is told of an ask, once each."""

    GRANTED = "granted"  # while its lease lives
    ENDED = "ended"  # out of line without a lease
    RELEASED = "released"  # the lease, given back
    EXPIRED = "expired"  # the lease, past its expiry
    REVOKED = "revoked"  # the lease, taken back


FINAL = frozenset({Answer.ENDED, Answer.RELEASED, Answer.EXPIRED, Answer.REVOKED})
"""The answers after which nothing more comes of an ask."""


GRANTED = (
    "Your request {request} in line was granted: lease {lease}, token {token}. "
    "Every act on the resource presents the token. Go on with the work that "
    "needed it."
)
ENDED = (
    "Your request {request} left its line without a lease: {why}. Ask again "
    "if the work still needs the resource."
)
LEASE_ENDED = (
    "The lease {lease} granted to your request {request} {why}: the resource "
    "is no longer yours, and an act under token {token} is refused. Ask again "
    "if the work still needs it."
)
LEASE_ENDS: dict[LeaseStatus, tuple[Answer, str]] = {
    LeaseStatus.RELEASED: (Answer.RELEASED, "was released"),
    LeaseStatus.EXPIRED: (Answer.EXPIRED, "ran past its expiry"),
    LeaseStatus.REVOKED: (Answer.REVOKED, "was revoked"),
}
EXPIRED = "it waited past its wait"
WHY: dict[EndReason, str] = {
    EndReason.ASKED: "it was cancelled",
    EndReason.WAITER_GONE: "its session no longer waited",
    EndReason.REFUSED: "the resource's kind refused it",
    EndReason.RETIRED: "the resource it named was retired",
}


@dataclass(frozen=True)
class Ask:
    """An ask a loop made: its request, whether the call's answer carried
    the lease, which then needs no notice, and that answer's step, from
    which each notice of the ask takes its id."""

    request_id: UUID
    granted: bool
    answer_id: UUID
    answered_at: datetime


def in_line(request: LeaseRequest, session_id: UUID, key: UUID) -> LeaseRequest:
    """The ask of a tool call: the call's key is the request's id and its
    key, so a repeat after a crash answers the request the first made and
    joins no line twice, and the session that made the call is its waiter."""
    return request.model_copy(
        update={
            "id": key,
            "idempotency_key": key,
            "waiter_kind": WaiterKind.SESSION,
            "waiter_id": session_id,
        }
    )


def asks(steps: Sequence[Step], tools: Collection[str]) -> list[Ask]:
    """The asks of a session, in order, across its loops: each answer
    without a failure to a call of one of `tools`, read from what the call
    answered. A lease outlives the loop that took it, so its end is told in
    a later one."""
    called = {
        step.id
        for step in steps
        if isinstance(step.header, ToolRequestHeader) and step.header.tool in tools
    }
    found: list[Ask] = []
    for step in steps:
        header = step.header
        if not isinstance(header, ToolResponseHeader) or header.failure is not None:
            continue
        if step.responds_to not in called:
            continue
        answer = _answer(step)
        if answer is not None:
            granted = answer.lease_id is not None
            found.append(Ask(answer.request_id, granted, step.id, step.created_at))
    return found


def _answer(response: Step) -> InLine | None:
    """What a call that asked in line answered: the fields of `InLine` in
    its result, whatever else a tool built on it adds."""
    text = "".join(
        part.text for part in response.as_tool_response().parts if isinstance(part, TextBlock)
    )
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    fields = {name: data[name] for name in InLine.model_fields if name in data}
    try:
        return InLine.model_validate(fields)
    except ValidationError:
        return None


def notice_id(ask: Ask, answer: Answer) -> UUID:
    """The id of the one notice that tells an ask's answer, derived from the
    step of the call's answer."""
    return derived_id(ask.answer_id, ask.answered_at, f"lease_notice:{answer.value}")


def done(ask: Ask, told: Collection[UUID]) -> bool:
    """Whether the model was told an ask's final answer: nothing more comes
    of it, and it is not read again."""
    return any(notice_id(ask, answer) in told for answer in FINAL)


def untold(standing: Standing, ask: Ask, told: Collection[UUID]) -> list[tuple[UUID, str]]:
    """The notice an ask's standing owes the model, with its id, once: the
    grant its call did not answer with, only while the lease lives; the
    lease's end, whether it was released, expired, or revoked, in its
    stead once it ended; or the request's end without a lease. A request
    that waits owes nothing."""
    request = standing.request
    lease = standing.lease
    if request.status is RequestStatus.GRANTED and lease is not None:
        ended = LEASE_ENDS.get(lease.status)
        if ended is not None:
            answer, why = ended
            text = LEASE_ENDED.format(
                lease=lease.id, request=request.id, why=why, token=lease.token
            )
        elif ask.granted:
            return []
        else:
            answer = Answer.GRANTED
            text = GRANTED.format(request=request.id, lease=lease.id, token=lease.token)
    elif request.status in (RequestStatus.CANCELLED, RequestStatus.EXPIRED):
        reason = request.end_reason
        answer = Answer.ENDED
        text = ENDED.format(request=request.id, why=EXPIRED if reason is None else WHY[reason])
    else:
        return []
    step_id = notice_id(ask, answer)
    return [] if step_id in told else [(step_id, text)]


def line_park(waiting: Sequence[Standing]) -> Park:
    """The park of a loop whose asks wait: it names the one nearest its
    grant, its place and the estimate of its wait. Only the grant, or the
    request's end, clears it; the request's own wait bounds it."""
    first = min(
        waiting,
        key=lambda standing: (standing.place is None, standing.place or 0),
    )
    return Park(reason=ParkReason.RESOURCE, unlock=LINE_UNLOCK, line=_line(first))


def job_line_park(standing: Standing, job: JobPark, deadline: datetime) -> Park:
    """The park of a job's call whose grant starts the job: in line, with
    its place and estimate, and the job it waits to start. The grant, or the
    request's end, clears it, and the job's deadline bounds it."""
    return Park(
        reason=ParkReason.RESOURCE,
        unlock=LINE_UNLOCK,
        retry_at=deadline,
        job=job,
        line=_line(standing),
    )


def _line(standing: Standing) -> LinePark:
    request = standing.request
    return LinePark(
        request_id=request.id,
        kind=request.kind.value,
        resource_id=request.resource_id,
        place=standing.place,
        estimate_seconds=standing.estimate_seconds,
    )


UNSTARTED = "The job did not start: its request {request} left its line without a lease: {why}."
FAILS: dict[EndReason | None, ToolFailure] = {
    None: ToolFailure.TIMEOUT,  # it waited past its wait
    EndReason.ASKED: ToolFailure.INTERRUPTED,
    EndReason.WAITER_GONE: ToolFailure.INTERRUPTED,
    EndReason.REFUSED: ToolFailure.DENIED,
    EndReason.RETIRED: ToolFailure.PERMANENT,
}


def unstarted(request: LeaseRequest) -> tuple[str, ToolFailure]:
    """What a job's call answers when its request left the line without a
    lease, so no grant started the job, and the class of that failure."""
    reason = request.end_reason
    why = EXPIRED if reason is None else WHY[reason]
    return UNSTARTED.format(request=request.id, why=why), FAILS[reason]


def unheard(steps: Sequence[Step], response: Step) -> bool:
    """Whether something the model has not read landed after the request
    `response` answers: the engine's notice, or a waking input."""
    since = next((step.seq for step in steps if step.id == response.responds_to), response.seq)
    return any(
        step.seq > since
        and step.type.is_input()
        and (
            step.actor is Actor.ENGINE
            or (isinstance(step.header, InputHeader) and step.header.waking)
        )
        for step in steps
    )
