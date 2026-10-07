"""A gate that parks, over the memory storage and the scripted provider: a
gate someone else supplies parks the loop on a reason and an unlock of its
own (`GateParked`), and a call or a job nobody can be named to pay for
(`SpenderUnknown`) parks it for a person. Each holds on a model call's
path and a spending job's: nothing is held, nothing is called or started,
and nothing raises out of the loop."""

from datetime import datetime
from pathlib import Path
from uuid import UUID

import pytest
from contracts.loops import ASSISTANT, BUILDER, DELIVERY, Clock, call, loop_over, reply, said

from acme.integrations.model_providers.calls import ModelCall
from acme.om.agents.loop_rules import SPENDER_UNLOCK
from acme.om.agents.types.run import RunEnd
from acme.om.attribution.types.principal import Principal
from acme.om.context import TenantContext
from acme.om.exceptions import GateParked, PlatformException, SpenderUnknown
from acme.om.models.types.fill import Fill, ModelRole
from acme.om.root import Managers
from acme.om.steps.types.header import Park, ParkReason
from acme.om.steps.types.step import StepType
from acme.om.windows.impl.gate import CallGateBudgetImpl

NORM = Park(reason=ParkReason.PERSON, unlock="norm")
"""A gate's own park: a call far above its session's norm, for a person."""


class Parking(CallGateBudgetImpl):
    """The budgets' gate, behind one that raises `turn` at a model call's
    hold and `job` at a spending job's, and counts the holds it let through."""

    def __init__(
        self,
        managers: Managers,
        clock: Clock,
        *,
        turn: PlatformException | None = None,
        job: PlatformException | None = None,
    ) -> None:
        super().__init__(
            managers.budget_gate, managers.pricing, managers.agent_sessions, managers.budgets, clock
        )
        self.turn, self.job = turn, job
        self.held = 0

    async def authorize(
        self,
        ctx: TenantContext,
        session_id: UUID,
        spender: Principal,
        role: ModelRole,
        fill: Fill,
        call: ModelCall,
        *,
        credential: str,
    ) -> UUID:
        if self.turn is not None:
            raise self.turn
        self.held += 1
        return await super().authorize(
            ctx, session_id, spender, role, fill, call, credential=credential
        )

    async def authorize_job(
        self,
        ctx: TenantContext,
        session_id: UUID,
        spender: Principal,
        tool: str,
        rate_micros_per_hour: int,
        deadline: datetime,
    ) -> UUID:
        if self.job is not None:
            raise self.job
        self.held += 1
        return await super().authorize_job(
            ctx, session_id, spender, tool, rate_micros_per_hour, deadline
        )


CASES = [
    (GateParked(NORM, "far above the session's norm"), NORM),
    (
        SpenderUnknown("the engine cannot tell who pays for this call"),
        Park(reason=ParkReason.PERSON, unlock=SPENDER_UNLOCK),
    ),
]
IDS = ["gate-parked", "spender-unknown"]


@pytest.mark.parametrize(("raised", "park"), CASES, ids=IDS)
async def test_a_gate_that_parks_a_model_call_parks_the_loop_where_it_says(
    tmp_path: Path, raised: PlatformException, park: Park
) -> None:
    gates: list[Parking] = []

    def parking(managers: Managers, clock: Clock) -> Parking:
        gates.append(Parking(managers, clock, turn=raised))
        return gates[-1]

    loop = loop_over(tmp_path, call_gate=parking)
    session_id = await loop.start()
    await loop.say(session_id, "What is the total?")
    loop.anthropic.add(reply(said("Never asked.")))

    run = await loop.loops.run(loop.owner, session_id)

    assert run.end is RunEnd.PARKED and run.park is not None
    assert (run.park.reason, run.park.unlock, run.park.retry_at) == (
        park.reason,
        park.unlock,
        park.retry_at,
    )
    assert gates[0].held == 0, "nothing was held"
    assert loop.anthropic.calls == [] and loop.anthropic.remaining == 1, "no call was made"
    steps = await loop.history(session_id)
    assert not [step for step in steps if step.type is StepType.MODEL_REQUEST]
    assert steps[-1].type is StepType.PARKED


@pytest.mark.parametrize(("raised", "park"), CASES, ids=IDS)
async def test_a_gate_that_parks_a_spending_job_parks_the_loop_and_starts_nothing(
    tmp_path: Path, raised: PlatformException, park: Park
) -> None:
    gates: list[Parking] = []

    def parking(managers: Managers, clock: Clock) -> Parking:
        gates.append(Parking(managers, clock, job=raised))
        return gates[-1]

    loop = loop_over(tmp_path, kinds=(ASSISTANT, DELIVERY, BUILDER), call_gate=parking)
    compute = loop.jobs["compute"]
    session_id = await loop.start("builder")
    await loop.say(session_id, "Start it.")
    loop.anthropic.add(reply(call("compute", q="everything")), reply(said("It is done.")))

    run = await loop.loops.run(loop.owner, session_id)

    assert run.end is RunEnd.PARKED and run.park is not None
    assert (run.park.reason, run.park.unlock, run.park.job) == (park.reason, park.unlock, None)
    assert compute.started == {}, "no job started"
    assert gates[0].held == 1, "the model call's hold alone: none for the job"
    steps = await loop.history(session_id)
    (request,) = [step for step in steps if step.type is StepType.TOOL_REQUEST]
    assert not [step for step in steps if step.responds_to == request.id], "the call waits"
