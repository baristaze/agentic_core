"""What reaches a loop from outside a run: a person takes its
environment over, fencing every run, and gives it back with a summary
the model reads; a job's tool reports its completion, which wakes the
loop that waits on it."""

from uuid import UUID

from acme.om.agent_sessions.rules import unlock_step
from acme.om.agent_sessions.types.agent_session import AgentSession
from acme.om.agent_steps.rules import completion_step, control_step, origin_of
from acme.om.agent_steps.types.agent_step import Actor, AgentStep, AgentStepType
from acme.om.agent_steps.types.content import Content, TextBlock
from acme.om.agent_steps.types.header import (
    ControlCommand,
    InputHeader,
    Park,
    ParkReason,
)
from acme.om.agents import loop_rules as rules
from acme.om.agents.impl.loop_core import (
    LoopCore,
)
from acme.om.attribution.rules import in_person
from acme.om.attribution.types.principal import Principal, PrincipalKind
from acme.om.base import new_id
from acme.om.context import Permission, TenantContext
from acme.om.exceptions import (
    Conflict,
    NotAuthorized,
    NotFound,
    ValidationFailed,
)
from acme.om.tools.types.call import (
    JobCompletion,
)

HANDOVER_UNLOCK = "give_back"


class LoopTakeOver(LoopCore):
    """What reaches a loop from outside a run: a person taking its
    environment over and giving it back, and a job's completion."""

    async def take_over(self, ctx: TenantContext, agent_session_id: UUID) -> AgentSession:
        _by_hand(ctx)
        session = await self._sessions.project_status(ctx, agent_session_id)
        if _held_by_another(session.park, ctx):
            raise Conflict(f"agent session {agent_session_id} is held by another person")
        # Only one who takes part takes the environment, its steerer or a
        # participant, so the summary they give back is theirs to write.
        await self._attribution.steers(ctx, agent_session_id)
        epoch = await self._steps.begin_run(ctx, agent_session_id)
        history = await self._history(ctx, agent_session_id)
        loop = rules.open_loop(history)
        loop_id = new_id() if loop is None else loop.loop_id
        park = Park(reason=ParkReason.HANDOVER, unlock=HANDOVER_UNLOCK, holder=ctx.user_id)
        return await self._sessions.park(ctx, agent_session_id, epoch, loop_id, park)

    async def give_back(
        self, ctx: TenantContext, agent_session_id: UUID, summary: str
    ) -> AgentSession:
        _by_hand(ctx)
        session = await self._sessions.project_status(ctx, agent_session_id)
        if session.park is None or session.park.reason is not ParkReason.HANDOVER:
            raise ValidationFailed(f"agent session {agent_session_id} is not handed over")
        if _held_by_another(session.park, ctx):
            raise NotAuthorized(f"agent session {agent_session_id} is held by another person")
        epoch = await self._steps.begin_run(ctx, agent_session_id)
        loop = rules.open_loop(await self._history(ctx, agent_session_id))
        if loop is None:
            raise ValidationFailed(f"agent session {agent_session_id} holds no loop to give back")
        now = self._clock()
        changed = rules.changed_step(new_id(), now, agent_session_id, loop.loop_id)
        await self._steps.append_agent_steps(ctx, agent_session_id, epoch, [changed])
        message_id = new_id()
        message = AgentStep(
            id=message_id,
            created_at=now,
            agent_session_id=agent_session_id,
            loop_id=message_id,
            type=AgentStepType.MESSAGE,
            actor=Actor.PERSON,
            origin=origin_of(ctx.app.type),
            header=InputHeader(
                waking=True, principal=Principal(kind=PrincipalKind.PERSON, id=ctx.user_id)
            ),
            content=Content(blocks=(TextBlock(text=summary),)),
        )
        # The holder's own unlock, in their name: the one a held hand-over
        # takes (`agent_sessions.rules.clears`).
        unlock = control_step(new_id(), now, agent_session_id, ctx, ControlCommand.UNLOCK)
        await self._steps.append_inputs(ctx, agent_session_id, [message, unlock])
        return await self._sessions.project_status(ctx, agent_session_id)

    async def complete_job(
        self, ctx: TenantContext, agent_session_id: UUID, completion: JobCompletion
    ) -> AgentSession:
        ctx.require(Permission.WRITE)
        # Another tenant's session, or a deleted one, is not found here.
        await self._sessions.project_status(ctx, agent_session_id)
        history = await self._history(ctx, agent_session_id)
        loop = rules.open_loop(history)
        started = None if loop is None else rules.started_jobs(history, loop.loop_id)
        waiting = None if started is None else started.get(completion.key)
        if (
            loop is None
            or waiting is None
            or waiting.job.handle != completion.handle
            or waiting.completion is not None
        ):
            raise NotFound(f"no job {completion.key} waits in agent session {agent_session_id}")
        now = self._clock()
        event = completion_step(
            new_id(), now, ctx, agent_session_id, completion.key, completion.model_dump_json()
        )
        inputs = [event]
        park = loop.park
        if park is not None and park.job is not None and park.job.key == completion.key:
            # The loop still waits on this job: its park is cleared. A loop
            # that parked since for another reason reads the completion when
            # that park clears.
            inputs.append(unlock_step(new_id(), agent_session_id, now))
        await self._steps.append_inputs(ctx, agent_session_id, inputs)
        return await self._sessions.project_status(ctx, agent_session_id)


def _by_hand(ctx: TenantContext) -> None:
    """A person who may write, acting themselves: the environment is taken
    and given back by hand, never by a program on an API key."""
    ctx.require(Permission.WRITE)
    if not in_person(ctx):
        raise NotAuthorized("the environment is taken over and given back by a person, in person")


def _held_by_another(park: Park | None, ctx: TenantContext) -> bool:
    """Whether a hand-over names a holder other than the caller."""
    return (
        park is not None
        and park.reason is ParkReason.HANDOVER
        and park.holder is not None
        and park.holder != ctx.user_id
    )
