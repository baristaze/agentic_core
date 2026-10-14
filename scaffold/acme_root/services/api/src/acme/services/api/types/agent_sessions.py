"""The wire types of agent sessions: a session, one step of its history,
a tool output it keeps, and what a person sends it. A step's view is flat:
what it is, who wrote it, what it says as text, and the few fields of its
header a reader acts on. Its content is the session's, so whoever the
session is visible to reads it as the session's own reader does."""

from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from acme.integrations.model_providers.types import StopReason
from acme.om.agent_sessions.types.agent_session import AgentSessionStatus
from acme.om.agent_steps.types.agent_step import Actor, AgentStepType, Origin
from acme.om.agent_steps.types.content import MAX_NAME
from acme.om.agent_steps.types.header import ControlCommand, LoopOutcome, ParkReason, ToolFailure
from acme.om.agents.types.request import MAX_TITLE
from acme.om.attribution.types.authority import Visibility
from acme.om.attribution.types.principal import MAX_KIND
from acme.om.tools.types.call import MAX_REPORT
from acme.services.api.types.common import RequestBody, View

MAX_MESSAGE = 100_000
"""The most characters one message carries."""


class LineParkView(View):
    """The line a loop waits in: the request, what it asked for (a kind,
    and the resource when it named one), its place (1 is next in some line
    it stands in), and the estimate of its wait."""

    request_id: UUID
    kind: str
    resource_id: UUID | None
    place: int | None
    estimate_seconds: float | None


class ParkView(View):
    """Why a parked loop waits, what clears it, and when it tries again by
    itself; a park only a person clears has no time, and neither has a park
    in line, which names where it stands. `cause` says why in words when the
    engine can name it, such as a workspace base still being built, or a
    key a provider refused and who must act. A hand-over names `holder`,
    the user who holds the environment and alone gives it back."""

    reason: ParkReason
    unlock: str
    retry_at: datetime | None
    line: LineParkView | None = None
    cause: str | None
    holder: UUID | None = None


class AgentSessionView(View):
    """A session: its kind, its title, and its status, which follows its
    steps. `pending` while an input waits for a run, `running` while a run
    holds its loop, `parked` while the loop waits, `idle` when no loop is
    open. `visibility` says who sees it: every member of the org, who may
    join it, or its creator and its `participants` alone. `steerer` is the
    person whose messages instruct it, its creator until a hand-off; a
    participant's message is data. A sub-agent names none: its parent
    steers it, and it is seen as its root is."""

    id: UUID
    title: str
    kind: str
    kind_version: int
    status: AgentSessionStatus
    park: ParkView | None
    created_at: datetime
    created_by: UUID
    archived_at: datetime | None
    visibility: Visibility
    participants: list[UUID]
    steerer: UUID | None
    parent_id: UUID | None


class AgentSessionPageView(View):
    """One page of root sessions by id. With `has_more`, the next page
    starts after the last one's id."""

    items: list[AgentSessionView]
    has_more: bool


class AgentStepUsageView(View):
    """What a model call used, as its provider reported it, in disjoint
    classes, so no token is counted twice."""

    input: int
    cache_read: int
    cache_write: int
    output: int
    thinking: int


class SnapshotView(View):
    """A workspace's snapshot a step names: its id, which a restore names,
    the hash of its bytes keyed by its session, its size in bytes, and the
    workspace it was taken from."""

    id: UUID
    hash: str
    size: int
    workspace_id: UUID


class AgentStepView(View):
    """One step of a session's history, in its order. `text` is what it
    says: a message's words, a model's answer, a tool's result. The rest is
    its header's, by type: the tools a model response called, why it
    stopped, and what it used; a tool call's tool and the class of its
    failure; a control's command; a park; a loop's outcome, and its cause
    when the engine names one, such as the setup command that failed and
    its exit code; the snapshot a `snapshotted` step names, or a restore
    starts from. A message names its `author`, and `participant` says it is
    a participant's, data, never an instruction. A `switched` step that
    moved the steer names the new `steerer`, and one that switched who sees
    the session its `visibility`."""

    id: UUID
    seq: int
    loop_id: UUID
    type: AgentStepType
    actor: Actor
    origin: Origin
    responds_to: UUID | None
    refs: list[UUID]
    created_at: datetime
    text: str
    tools: list[str]
    stop_reason: StopReason | None
    usage: AgentStepUsageView | None
    tool: str | None
    failure: ToolFailure | None
    command: ControlCommand | None
    park: ParkView | None
    outcome: LoopOutcome | None
    cause: str | None
    snapshot: SnapshotView | None
    author: UUID | None
    participant: bool
    steerer: UUID | None
    visibility: Visibility | None


class AgentStepPageView(View):
    """One page of a session's history, after the seq the request named.
    With `has_more`, the next page starts after the last step's seq."""

    items: list[AgentStepView]
    has_more: bool


class ToolOutputPageView(View):
    """A page of what a session keeps whole outside its window, such as a
    long tool result: its characters from `start`, the whole tool output's
    length, and whether more follow."""

    tool_output_id: UUID
    start: int
    text: str
    characters: int
    has_more: bool


class StartAgentSessionRequest(RequestBody):
    """A session to start on the latest version of a kind the product runs."""

    kind: str = Field(min_length=1, max_length=MAX_KIND)
    title: str = Field(min_length=1, max_length=MAX_TITLE)


class MessageRequest(RequestBody):
    """A message to the session, said in the caller's name. It wakes an idle
    session, and a running loop reads it at its next model request."""

    text: str = Field(min_length=1, max_length=MAX_MESSAGE)


class AgentSessionControl(StrEnum):
    """The controls a person sends a session out of band. A decision on one
    tool call is a route of its own."""

    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    INTERRUPT = "interrupt"
    COMPACT = "compact"
    UNLOCK = "unlock"
    RESTORE = "restore"


class ControlRequest(RequestBody):
    """A control. An interrupt names the seq of the tool request it stops,
    and a restore the id of a snapshot the session's history names, which
    its workspace's next prepare starts from; no other control names
    either."""

    command: AgentSessionControl
    request_seq: int | None = Field(default=None, ge=1)
    snapshot_id: UUID | None = None

    @model_validator(mode="after")
    def _an_interrupt_names_its_call(self) -> Self:
        if (self.command is AgentSessionControl.INTERRUPT) != (self.request_seq is not None):
            raise ValueError("an interrupt names the call it stops, and no other control names one")
        if (self.command is AgentSessionControl.RESTORE) != (self.snapshot_id is not None):
            raise ValueError(
                "a restore names the snapshot it starts from, and no other control names one"
            )
        return self


class DecisionRequest(RequestBody):
    """A person's decision on the tool call at a seq. A denial's note is what
    the model reads."""

    approve: bool
    note: str = Field(default="", max_length=MAX_MESSAGE)


class DeadlineRequest(RequestBody):
    """The deadline the session's tree shares, set whole: an instant, or
    null to take it away. One past now, or none, unlocks every session of
    the tree parked on the deadline. An instant with no offset names no
    moment, so it is refused."""

    deadline: AwareDatetime | None


class VisibilityRequest(RequestBody):
    """Who sees the session from here: every member of the org, or its
    creator and its participants alone."""

    visibility: Visibility


class PrincipalRequest(RequestBody):
    """Who steers the session from here: a participant its steerer hands it
    to. Left out, the caller takes it."""

    steerer: UUID | None = None


class GiveBackRequest(RequestBody):
    """What the person who held the environment did there, which the model
    reads as their message when the loop goes on."""

    summary: str = Field(min_length=1, max_length=MAX_MESSAGE)


class JobCompletionRequest(RequestBody):
    """How a job ended, from the system it ran on: the tool's handle for
    the work, its result as text, and the class of its failure when it
    failed. A job that spends is counted at its whole hold: no cost is
    taken over the wire."""

    handle: str = Field(min_length=1, max_length=MAX_NAME)
    text: str = Field(default="", max_length=MAX_REPORT)
    failure: ToolFailure | None = None
