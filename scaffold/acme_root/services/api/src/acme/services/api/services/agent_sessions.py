"""The agent sessions service: what the wire can do with a session, in
views. A session is started, listed, joined, spoken to, steered, and read;
its loop runs in the session runner, never here. Each one is read as the
person sees it: one not visible to them answers as one that never
existed."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.agent_sessions.types.agent_session import AgentSessionStatus, Among
from acme.om.context import TenantContext
from acme.services.api.types.agent_sessions import (
    AgentSessionPageView,
    AgentSessionView,
    AgentStepPageView,
    AgentStepView,
    ArtifactPageView,
    ControlRequest,
    DeadlineRequest,
    DecisionRequest,
    GiveBackRequest,
    JobCompletionRequest,
    MessageRequest,
    PrincipalRequest,
    StartAgentSessionRequest,
    VisibilityRequest,
)


class AgentSessionsServiceInterface(ABC):
    @abstractmethod
    async def start_agent_session(
        self, ctx: TenantContext, body: StartAgentSessionRequest, agent_session_id: UUID
    ) -> AgentSessionView:
        """A session on the latest version of the kind, idle, under
        `agent_session_id`, the id the idempotency record minted. A kind the
        product does not run is refused."""
        ...

    @abstractmethod
    async def get_agent_session(
        self, ctx: TenantContext, agent_session_id: UUID
    ) -> AgentSessionView: ...

    @abstractmethod
    async def list_sessions(
        self,
        ctx: TenantContext,
        among: Among,
        status: AgentSessionStatus | None,
        after: UUID | None,
        limit: int,
    ) -> AgentSessionPageView:
        """A page of the org's root sessions: the ones the caller made or
        takes part in, or every one they see."""
        ...

    @abstractmethod
    async def add_participant(
        self, ctx: TenantContext, agent_session_id: UUID, user_id: UUID
    ) -> AgentSessionView:
        """`user_id` takes part in the session: the caller joining an
        org-wide one, or anyone its creator adds."""
        ...

    @abstractmethod
    async def remove_participant(
        self, ctx: TenantContext, agent_session_id: UUID, user_id: UUID
    ) -> AgentSessionView:
        """`user_id` takes no part in the session: its creator removing
        them, or they leaving on their own, never while they steer it."""
        ...

    @abstractmethod
    async def switch_visibility(
        self, ctx: TenantContext, agent_session_id: UUID, body: VisibilityRequest
    ) -> AgentSessionView:
        """Its creator switches who sees the session, recorded as a step."""
        ...

    @abstractmethod
    async def send_message(
        self, ctx: TenantContext, agent_session_id: UUID, body: MessageRequest, agent_step_id: UUID
    ) -> AgentStepView:
        """The message, durable when this answers, under `agent_step_id`; the
        session's status follows it, and an idle session wakes."""
        ...

    @abstractmethod
    async def send_control(
        self, ctx: TenantContext, agent_session_id: UUID, body: ControlRequest, agent_step_id: UUID
    ) -> AgentStepView:
        """The control, durable when this answers, under `agent_step_id`; a run
        reads it between steps and while a tool runs. An interrupt names the
        tool request it stops by its seq: `NotFound` when none is there."""
        ...

    @abstractmethod
    async def decide_call(
        self, ctx: TenantContext, agent_session_id: UUID, request_seq: int, body: DecisionRequest
    ) -> AgentStepView:
        """A person's decision on the tool request at `request_seq`, bound to
        its tool and its input; the session's status follows it."""
        ...

    @abstractmethod
    async def set_deadline(
        self, ctx: TenantContext, agent_session_id: UUID, body: DeadlineRequest
    ) -> AgentSessionView:
        """The deadline of the session's tree, moved for every session of
        it; one past now, or none, unlocks each session parked on it. The
        session as it stands after."""
        ...

    @abstractmethod
    async def assign_principal(
        self, ctx: TenantContext, agent_session_id: UUID, body: PrincipalRequest | None
    ) -> AgentSessionView:
        """The steer moves, recorded as a step: its steerer hands it to the
        participant the body names, or with none the caller takes it, its
        steerer already or, for a steerer who lapsed, one who holds
        `manage_agent_sessions`. The steerer is the principal the session's tool
        calls run under, and a loop parked on a principal who no longer
        holds is unlocked. The session as it stands after."""
        ...

    @abstractmethod
    async def take_over(self, ctx: TenantContext, agent_session_id: UUID) -> AgentSessionView:
        """The caller, in person, takes the session's environment to work by
        hand: its loop parks on a hand-over the caller holds."""
        ...

    @abstractmethod
    async def give_back(
        self, ctx: TenantContext, agent_session_id: UUID, body: GiveBackRequest
    ) -> AgentSessionView:
        """The holder, in person, gives the environment back with what they
        did, and the loop goes on."""
        ...

    @abstractmethod
    async def complete_job(
        self, ctx: TenantContext, agent_session_id: UUID, key: UUID, body: JobCompletionRequest
    ) -> AgentSessionView:
        """A job's completion, from the system it ran on: kept only for a job
        of that key and handle the session's open loop waits on, and its
        park is unlocked; anything else is `NotFound`."""
        ...

    @abstractmethod
    async def get_agent_steps(
        self, ctx: TenantContext, agent_session_id: UUID, after_seq: int, limit: int
    ) -> AgentStepPageView: ...

    @abstractmethod
    async def get_artifact(
        self, ctx: TenantContext, agent_session_id: UUID, artifact_id: UUID, start: int, limit: int
    ) -> ArtifactPageView:
        """A page of an artifact the session keeps, from the character at
        `start`, read as its steps are."""
        ...
