"""Step storage for a memory-only session that keeps its shape: the shape
goes to the history, and what a step says stays in this process, beside
it. Nothing a step says reaches the history, sealed or not.

The history numbers the steps and fences the writer, as it does for any
session. A read takes the shape from it and puts back what this process
still holds; a step whose content this process does not hold, after a
restart or from another process, reads as absent."""

from collections.abc import Sequence
from uuid import UUID

from acme.om.agent_steps.storage import AgentStepStorageInterface
from acme.om.agent_steps.types.agent_step import AgentStep
from acme.om.agent_steps.types.content import Children, Content
from acme.om.agent_steps.types.page import AgentStepCursor
from acme.om.privacy.impl.sealed_agent_steps import absent, refuse_sealed, says_something


class AgentStepStorageShapeOnlyImpl(AgentStepStorageInterface):
    def __init__(self, shapes: AgentStepStorageInterface) -> None:
        self._shapes = shapes
        # What each step said, by tenant and step, while this process holds it.
        self._held: dict[tuple[UUID, UUID], tuple[Content, Children]] = {}

    async def begin_run(self, org_id: UUID, agent_session_id: UUID) -> int:
        return await self._shapes.begin_run(org_id, agent_session_id)

    async def append_agent_steps(
        self, org_id: UUID, agent_session_id: UUID, epoch: int, steps: Sequence[AgentStep]
    ) -> tuple[AgentStep, ...]:
        stored = await self._shapes.append_agent_steps(
            org_id, agent_session_id, epoch, _shapes(steps)
        )
        return self._kept(org_id, steps, stored)

    async def append_inputs(
        self, org_id: UUID, agent_session_id: UUID, steps: Sequence[AgentStep]
    ) -> tuple[AgentStep, ...]:
        stored = await self._shapes.append_inputs(org_id, agent_session_id, _shapes(steps))
        return self._kept(org_id, steps, stored)

    async def read_agent_steps(
        self, org_id: UUID, agent_session_id: UUID, after_seq: int, limit: int
    ) -> list[AgentStep]:
        stored = await self._shapes.read_agent_steps(org_id, agent_session_id, after_seq, limit)
        return [self._filled(org_id, step) for step in stored]

    async def read_cursor(self, org_id: UUID, agent_session_id: UUID) -> AgentStepCursor:
        return await self._shapes.read_cursor(org_id, agent_session_id)

    async def purge_history(self, org_id: UUID, agent_session_id: UUID, limit: int) -> int:
        return await self._shapes.purge_history(org_id, agent_session_id, limit)

    async def purge_tenant(self, org_id: UUID, limit: int) -> int:
        return await self._shapes.purge_tenant(org_id, limit)

    def _kept(
        self, org_id: UUID, steps: Sequence[AgentStep], stored: Sequence[AgentStep]
    ) -> tuple[AgentStep, ...]:
        """Holds what each appended step said, the first time its id is
        kept, and answers the stored steps with it."""
        for step in steps:
            if says_something(step):
                self._held.setdefault((org_id, step.id), (step.content, step.children))
        return tuple(self._filled(org_id, step) for step in stored)

    def _filled(self, org_id: UUID, step: AgentStep) -> AgentStep:
        held = self._held.get((org_id, step.id))
        if held is None or not step.content.is_absent():
            return step
        content, children = held
        return step.model_copy(update={"content": content, "children": children})


def _shapes(steps: Sequence[AgentStep]) -> list[AgentStep]:
    refuse_sealed(steps)
    return [absent(step) if says_something(step) else step for step in steps]
