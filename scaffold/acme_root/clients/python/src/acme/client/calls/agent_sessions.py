"""The agent_sessions namespace's calls: sessions with an agent, started on
a kind, spoken to, steered, and read. The loop runs in the session runner;
a send answers once it is durable."""

from typing import Any
from uuid import UUID, uuid4

from acme.client.transport import LIMIT_MAX, Transport
from acme.client.types.agent_sessions import (
    AgentSessionControl,
    AgentSessionView,
    AgentStepPageView,
    AgentStepView,
)


class AgentSessionsCalls(Transport):
    """An agent session's start, its messages and controls, and its history."""

    async def start_agent_session(
        self, kind: str, title: str, *, idempotency_key: str | None = None
    ) -> AgentSessionView:
        """A session on the latest version of `kind`, idle until a message
        wakes it. Always under an idempotency key."""
        started = await self.request(
            "POST",
            "/v1/agent-sessions",
            json={"kind": kind, "title": title},
            idempotency_key=idempotency_key or str(uuid4()),
        )
        return AgentSessionView.model_validate(started)

    async def agent_session(self, agent_session_id: UUID) -> AgentSessionView:
        read = await self.request("GET", f"/v1/agent-sessions/{agent_session_id}")
        return AgentSessionView.model_validate(read)

    async def send_message(
        self, agent_session_id: UUID, text: str, *, idempotency_key: str | None = None
    ) -> AgentStepView:
        """The message as stored, durable when this returns; a retry under
        the same key is the same step."""
        said = await self.request(
            "POST",
            f"/v1/agent-sessions/{agent_session_id}/messages",
            json={"text": text},
            idempotency_key=idempotency_key or str(uuid4()),
        )
        return AgentStepView.model_validate(said)

    async def send_control(
        self,
        agent_session_id: UUID,
        command: AgentSessionControl,
        *,
        request_seq: int | None = None,
        snapshot_id: UUID | None = None,
        idempotency_key: str | None = None,
    ) -> AgentStepView:
        """A control out of band; an interrupt names, by `request_seq`, the
        tool request it stops, and a restore, by `snapshot_id`, the snapshot
        of the session's that its workspace starts from next."""
        body: dict[str, Any] = {"command": command.value}
        if request_seq is not None:
            body["request_seq"] = request_seq
        if snapshot_id is not None:
            body["snapshot_id"] = str(snapshot_id)
        sent = await self.request(
            "POST",
            f"/v1/agent-sessions/{agent_session_id}/controls",
            json=body,
            idempotency_key=idempotency_key or str(uuid4()),
        )
        return AgentStepView.model_validate(sent)

    async def decide_call(
        self,
        agent_session_id: UUID,
        request_seq: int,
        *,
        approve: bool,
        note: str = "",
        idempotency_key: str | None = None,
    ) -> AgentStepView:
        """A decision on the tool call at `request_seq`, which the loop waits on."""
        decided = await self.request(
            "POST",
            f"/v1/agent-sessions/{agent_session_id}/calls/{request_seq}/decision",
            json={"approve": approve, "note": note},
            idempotency_key=idempotency_key or str(uuid4()),
        )
        return AgentStepView.model_validate(decided)

    async def agent_session_steps(
        self, agent_session_id: UUID, after_seq: int = 0, limit: int = LIMIT_MAX
    ) -> AgentStepPageView:
        """One page of the history, strictly after `after_seq`."""
        page = await self.request(
            "GET",
            f"/v1/agent-sessions/{agent_session_id}/steps",
            params={"after_seq": after_seq, "limit": limit},
        )
        return AgentStepPageView.model_validate(page)
