"""Pure rules of a session in line for a leased resource: the ask a tool's
call makes, the asks a loop holds, what each one's standing has not yet
told the model, and the park of a loop that waits on them. Values in,
values out; no clock, no storage.

A session waits in line as a waiter of the leases namespace. Its tool asks
and answers at once with its place; the loop parks only when the turn ends
with nothing else to do. A grant, a request's end without a lease, and a
revocation each reach the model as the engine's notice, written by a run
before its next model call. Each notice's id is its request's and its
answer's, so the history says what the model was told, after a lost run as
well."""

import json
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID, uuid5

from pydantic import ValidationError

from acme.om.agents.types.line import InLine
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
    LinePark,
    Park,
    ParkReason,
    ToolRequestHeader,
    ToolResponseHeader,
)
from acme.om.steps.types.step import Actor, Step

LINE_UNLOCK = "grant"
"""What clears a park in line: the grant, or its request's end without one."""


class Answer(StrEnum):
    """What the model is told of an ask, once each."""

    GRANTED = "granted"
    ENDED = "ended"  # out of line without a lease
    REVOKED = "revoked"


GRANTED = (
    "Your request {request} in line was granted: lease {lease}, token {token}. "
    "Every act on the resource presents the token. Go on with the work that "
    "needed it."
)
ENDED = (
    "Your request {request} left its line without a lease: {why}. Ask again "
    "if the work still needs the resource."
)
REVOKED = (
    "The lease {lease} granted to your request {request} was revoked: the "
    "resource is no longer yours, and an act under token {token} is refused."
)
EXPIRED = "it waited past its wait"
WHY: dict[EndReason, str] = {
    EndReason.ASKED: "it was cancelled",
    EndReason.WAITER_GONE: "its session no longer waited",
    EndReason.REFUSED: "the resource's kind refused it",
    EndReason.RETIRED: "the resource it named was retired",
}


@dataclass(frozen=True)
class Ask:
    """An ask a loop made: its request, and whether the call's answer
    carried the lease, which then needs no notice."""

    request_id: UUID
    granted: bool


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


def asks(steps: Sequence[Step], loop_id: UUID, tools: Collection[str]) -> list[Ask]:
    """The asks of a loop, in order: each answer without a failure to a call
    of one of `tools` the loop made, read from what the call answered."""
    called = {
        step.id
        for step in steps
        if isinstance(step.header, ToolRequestHeader)
        and step.loop_id == loop_id
        and step.header.tool in tools
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
            found.append(Ask(answer.request_id, answer.lease_id is not None))
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


def notice_id(request_id: UUID, answer: Answer) -> UUID:
    """The id of the one notice that tells an ask's answer."""
    return uuid5(request_id, answer.value)


def untold(standing: Standing, ask: Ask, told: Collection[UUID]) -> list[tuple[UUID, str]]:
    """The notices an ask's standing owes the model, each with its id: a
    grant its call did not answer with, an end without a lease, and a
    revocation, each once. A request that waits owes nothing."""
    request = standing.request
    owed: list[tuple[UUID, str]] = []

    def tell(answer: Answer, text: str) -> None:
        step_id = notice_id(request.id, answer)
        if step_id not in told:
            owed.append((step_id, text))

    lease = standing.lease
    if request.status is RequestStatus.GRANTED and lease is not None:
        if not ask.granted:
            tell(Answer.GRANTED, GRANTED.format(request=request.id, lease=lease.id, token=lease.token))
        if lease.status is LeaseStatus.REVOKED:
            tell(Answer.REVOKED, REVOKED.format(request=request.id, lease=lease.id, token=lease.token))
    elif request.status in (RequestStatus.CANCELLED, RequestStatus.EXPIRED):
        reason = request.end_reason
        why = EXPIRED if reason is None else WHY[reason]
        tell(Answer.ENDED, ENDED.format(request=request.id, why=why))
    return owed


def line_park(waiting: Sequence[Standing]) -> Park:
    """The park of a loop whose asks wait: it names the one nearest its
    grant, its place and the estimate of its wait. Only the grant, or the
    request's end, clears it; the request's own wait bounds it."""
    first = min(
        waiting,
        key=lambda standing: (standing.place is None, standing.place or 0),
    )
    request = first.request
    line = LinePark(
        request_id=request.id,
        kind=request.kind.value,
        resource_id=request.resource_id,
        place=first.place,
        estimate_seconds=first.estimate_seconds,
    )
    return Park(reason=ParkReason.RESOURCE, unlock=LINE_UNLOCK, line=line)


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
