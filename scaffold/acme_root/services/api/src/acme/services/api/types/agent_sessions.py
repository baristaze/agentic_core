"""The wire types of agent sessions: a session, one step of its history,
and what a person sends it. A step's view is flat: what it is, who wrote
it, what it says as text, and the few fields of its header a reader acts
on. Its content is the session's, so a reader of the org reads it as the
session's own reader does."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from acme.integrations.model_providers.types import StopReason
from acme.om.agent_sessions.types.agent_session import SessionStatus
from acme.om.agents.types.request import MAX_TITLE
from acme.om.attribution.types.principal import MAX_KIND
from acme.om.steps.types.header import ControlCommand, LoopOutcome, ParkReason, ToolFailure
from acme.om.steps.types.step import Actor, Origin, StepType
from acme.services.api.types.common import RequestBody, View

MAX_MESSAGE = 100_000
"""The most characters one message carries."""


class ParkView(View):
    """Why a parked loop waits, what clears it, and when it tries again by
    itself; a park only a person clears has no time."""

    reason: ParkReason
    unlock: str
    retry_at: datetime | None


class AgentSessionView(View):
    """A session: its kind, its title, and its status, which follows its
    steps. `pending` while an input waits for a run, `running` while a run
    holds its loop, `parked` while the loop waits, `idle` when no loop is
    open."""

    id: UUID
    title: str
    kind: str
    kind_version: int
    status: SessionStatus
    park: ParkView | None
    created_at: datetime
    created_by: UUID
    archived_at: datetime | None


class StepView(View):
    """One step of a session's history, in its order. `text` is what it
    says: a message's words, a model's answer, a tool's result. The rest is
    its header's, by type: the tools a model response called and why it
    stopped, a tool call's tool and the class of its failure, a control's
    command, a park, a loop's outcome."""

    id: UUID
    seq: int
    loop_id: UUID
    type: StepType
    actor: Actor
    origin: Origin
    responds_to: UUID | None
    refs: list[UUID]
    created_at: datetime
    text: str
    tools: list[str]
    stop_reason: StopReason | None
    tool: str | None
    failure: ToolFailure | None
    command: ControlCommand | None
    park: ParkView | None
    outcome: LoopOutcome | None


class StepPageView(View):
    """One page of a session's history, after the seq the request named.
    With `has_more`, the next page starts after the last step's seq."""

    items: list[StepView]
    has_more: bool


class StartSessionRequest(RequestBody):
    """A session to start on the latest version of a kind the product runs."""

    kind: str = Field(min_length=1, max_length=MAX_KIND)
    title: str = Field(min_length=1, max_length=MAX_TITLE)


class MessageRequest(RequestBody):
    """A message to the session, said in the caller's name. It wakes an idle
    session, and a running loop reads it at its next model request."""

    text: str = Field(min_length=1, max_length=MAX_MESSAGE)


class SessionControl(StrEnum):
    """The controls a person sends a session out of band. A decision on one
    tool call is a route of its own."""

    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    INTERRUPT = "interrupt"
    COMPACT = "compact"
    UNLOCK = "unlock"


class ControlRequest(RequestBody):
    command: SessionControl


class DecisionRequest(RequestBody):
    """A person's decision on the tool call at a seq. A denial's note is what
    the model reads."""

    approve: bool
    note: str = Field(default="", max_length=MAX_MESSAGE)
