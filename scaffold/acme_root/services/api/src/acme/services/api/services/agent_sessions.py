"""The agent sessions service: what the wire can do with a session, in
views. A session is started, spoken to, steered, and read; its loop runs in
the session runner, never here."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.context import TenantContext
from acme.services.api.types.agent_sessions import (
    AgentSessionView,
    ControlRequest,
    DeadlineRequest,
    DecisionRequest,
    GiveBackRequest,
    JobCompletionRequest,
    MessageRequest,
    StartSessionRequest,
    StepPageView,
    StepView,
)


class AgentSessionsServiceInterface(ABC):
    @abstractmethod
    async def start_session(
        self, ctx: TenantContext, body: StartSessionRequest, session_id: UUID
    ) -> AgentSessionView:
        """A session on the latest version of the kind, idle, under
        `session_id`, the id the idempotency record minted. A kind the
        product does not run is refused."""
        ...

    @abstractmethod
    async def get_session(self, ctx: TenantContext, session_id: UUID) -> AgentSessionView: ...

    @abstractmethod
    async def send_message(
        self, ctx: TenantContext, session_id: UUID, body: MessageRequest, step_id: UUID
    ) -> StepView:
        """The message, durable when this answers, under `step_id`; the
        session's status follows it, and an idle session wakes."""
        ...

    @abstractmethod
    async def send_control(
        self, ctx: TenantContext, session_id: UUID, body: ControlRequest, step_id: UUID
    ) -> StepView:
        """The control, durable when this answers, under `step_id`; a run
        reads it between steps and while a tool runs. An interrupt names the
        tool request it stops by its seq: `NotFound` when none is there."""
        ...

    @abstractmethod
    async def decide_call(
        self, ctx: TenantContext, session_id: UUID, request_seq: int, body: DecisionRequest
    ) -> StepView:
        """A person's decision on the tool request at `request_seq`, bound to
        its tool and its input; the session's status follows it."""
        ...

    @abstractmethod
    async def set_deadline(
        self, ctx: TenantContext, session_id: UUID, body: DeadlineRequest
    ) -> AgentSessionView:
        """The deadline of the session's tree, moved for every session of
        it; one past now, or none, unlocks each session parked on it. The
        session as it stands after."""
        ...

    @abstractmethod
    async def assign_principal(self, ctx: TenantContext, session_id: UUID) -> AgentSessionView:
        """The caller becomes the principal the session's tool calls run
        under, and a loop parked on a principal who no longer holds is
        unlocked. The session as it stands after."""
        ...

    @abstractmethod
    async def take_over(self, ctx: TenantContext, session_id: UUID) -> AgentSessionView:
        """The caller, in person, takes the session's environment to work by
        hand: its loop parks on a hand-over the caller holds."""
        ...

    @abstractmethod
    async def give_back(
        self, ctx: TenantContext, session_id: UUID, body: GiveBackRequest
    ) -> AgentSessionView:
        """The holder, in person, gives the environment back with what they
        did, and the loop goes on."""
        ...

    @abstractmethod
    async def complete_job(
        self, ctx: TenantContext, session_id: UUID, key: UUID, body: JobCompletionRequest
    ) -> AgentSessionView:
        """A job's completion, from the system it ran on: kept only for a job
        of that key and handle the session's open loop waits on, and its
        park is unlocked; anything else is `NotFound`."""
        ...

    @abstractmethod
    async def get_steps(
        self, ctx: TenantContext, session_id: UUID, after_seq: int, limit: int
    ) -> StepPageView: ...
