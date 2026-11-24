"""A kind that prepares its workspace early, over the memory storage, the
scripted provider, and the twin workspace provider (ADR 1048). Its run
starts the prepare beside its first model call, as that call goes out,
and the first call that reaches the workspace takes it: one prepare in
all. A run that parks before its call goes out starts none. A run of it that
reaches no workspace lets the instance go at its end, takes no snapshot
of it, and counts it by kind. A kind that declares nothing prepares at
its first call that reaches the workspace, and a run of it that reaches
none prepares none. An early prepare that fails, or that a restore
replaced since, leaves the first call to prepare as it would have, and no
prepare outlives its run."""

import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import SecretStr, ValidationError
from unit.test_loop_gates import NORM, Parking
from unit.test_loop_keys import Keyless

from acme.infra.agent_workspaces import (
    AgentWorkspace,
    Durability,
    EgressMode,
    EgressPolicy,
    IsolationMode,
    IsolationRefused,
    IsolationSpec,
)
from acme.infra.agent_workspaces.twin import AgentWorkspaceTwinImpl
from acme.infra.base import SYSTEM_SCOPE
from acme.infra.impl.local import InfraLocalImpl
from acme.infra.observability import WORKSPACE_PREPARES_UNUSED
from acme.infra.outages import Outage
from acme.integrations.model_providers.calls import Finished, ModelCall, StreamPart
from acme.om.agent_steps.rules import control_step
from acme.om.agent_steps.types.agent_step import AgentStep, AgentStepType
from acme.om.agent_steps.types.header import (
    ControlCommand,
    LoopEndedHeader,
    LoopOutcome,
    SnapshotHeader,
)
from acme.om.agents.types.kind import AgentKind
from acme.om.agents.types.run import RunEnd
from acme.om.base import new_id
from acme.om.context import TenantContext
from acme.om.exceptions import GateParked, OmException, SpendBudgetRefused
from acme.om.language_models.layer import LanguageModelsLayer
from acme.om.root import Managers
from acme.om.spend_budgets.types.amount import AmountUnit
from acme.om.spend_budgets.types.spend_breach import Refusal, SpendBreach, SpendBreachAction
from acme.om.tools.tool import ToolRuntime
from acme.om.tools.types.tool import ToolInput

from contracts.loops import (
    ASSISTANT,
    DELIVERY,
    Clock,
    Lookup,
    Loop,
    call,
    control,
    loop_over,
    of_type,
    reply,
    said,
    use,
)

TWIN = IsolationSpec(mode=IsolationMode.TWIN, egress=EgressPolicy(mode=EgressMode.NONE))
KEPT = TWIN.model_copy(update={"durability": Durability.SNAPSHOT})

EARLY = ASSISTANT.model_copy(
    update={
        "name": "early",
        "isolation": TWIN,
        "tools": ("lookup", "ledger"),
        "prepares_early": True,
    }
)
"""A kind whose runs reach the workspace early: `lookup` reaches it,
`ledger` does not."""
LATE = EARLY.model_copy(update={"name": "late", "prepares_early": False})
"""The same kind, declaring nothing."""
EARLY_KEPT = EARLY.model_copy(update={"name": "early_kept", "isolation": KEPT})
"""A kind that prepares early and keeps its workspace by snapshots."""

PREPARE = 0.05
"""How long the twin's prepare takes, in seconds: longer than a model call."""
MODEL = 0.01
"""How long a model call takes before it answers, in seconds."""


class Timed:
    """The twin provider and the scripted model, each taking a set time, and
    what happened in order: a prepare began (`prepare`) and ended
    (`prepared`), a model call answered (`answered`), and `lookup` ran in
    the workspace (`ran`). `prepares` holds the snapshot each prepare was
    asked to start from; `failing` refusals are raised by the next
    prepares, in order. A model call waits for `hold` when it is set, and
    `lookup` writes `writes` into the workspace when it is set."""

    def __init__(self, loop: Loop, monkeypatch: pytest.MonkeyPatch) -> None:
        twin = loop.infra.get_agent_workspaces()
        assert isinstance(twin, AgentWorkspaceTwinImpl)
        self.twin = twin
        self.events: list[str] = []
        self.prepares: list[bytes | None] = []
        self.released = 0
        self.snapshots = 0
        self.failing: list[IsolationRefused] = []
        self.live_when_ran: list[bool] = []
        self.began = asyncio.Event()  # set once a prepare began
        self.hold: asyncio.Event | None = None
        self.writes: bytes | None = None
        prepare, release, snapshot = twin.prepare, twin.release, twin.snapshot
        stream = loop.anthropic.stream
        lookup = loop.tools["lookup"]
        run = lookup.run

        async def prepared(
            org_id: UUID,
            agent_workspace_id: UUID,
            spec: IsolationSpec,
            snapshot: bytes | None = None,
            base: bytes | None = None,
            *,
            building: bool = False,
        ) -> AgentWorkspace:
            self.prepares.append(snapshot)
            self.events.append("prepare")
            self.began.set()
            await asyncio.sleep(PREPARE)
            if self.failing:
                self.events.append("refused")
                raise self.failing.pop(0)
            made = await prepare(
                org_id, agent_workspace_id, spec, snapshot, base, building=building
            )
            self.events.append("prepared")
            return made

        async def released(workspace: AgentWorkspace) -> None:
            self.released += 1
            await release(workspace)

        async def snapshotted(workspace: AgentWorkspace) -> bytes:
            self.snapshots += 1
            return await snapshot(workspace)

        async def streamed(
            model_call: ModelCall, *, credential: SecretStr | None = None
        ) -> AsyncIterator[StreamPart]:
            await asyncio.sleep(MODEL)
            if self.hold is not None:
                await self.hold.wait()
            async for part in stream(model_call, credential=credential):
                if isinstance(part, Finished):
                    self.events.append("answered")
                yield part

        async def ran(ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime) -> object:
            self.events.append("ran")
            self.live_when_ran.append(runtime.agent_session_id in twin.live)
            if self.writes is not None:
                twin.files[runtime.agent_session_id] = self.writes
            return await run(ctx, call_input, runtime)

        monkeypatch.setattr(twin, "prepare", prepared)
        monkeypatch.setattr(twin, "release", released)
        monkeypatch.setattr(twin, "snapshot", snapshotted)
        monkeypatch.setattr(loop.anthropic, "stream", streamed)
        monkeypatch.setattr(lookup, "run", ran)


def early_loop(
    tmp_path: Path,
    *,
    call_gate: Callable[[Managers, Clock], Parking] | None = None,
    language_models_layer: LanguageModelsLayer | None = None,
) -> Loop:
    return loop_over(
        tmp_path,
        kinds=(ASSISTANT, DELIVERY, EARLY, LATE, EARLY_KEPT),
        infra=InfraLocalImpl(tmp_path),
        extra=(Lookup("ledger", workspace=False),),
        call_gate=call_gate,
        language_models_layer=language_models_layer,
    )


def unused(kind: AgentKind) -> float:
    return WORKSPACE_PREPARES_UNUSED.labels(kind=kind.name)._value.get()


def others() -> list[asyncio.Task[object]]:
    """Every task still running but the test's own."""
    current = asyncio.current_task()
    return [task for task in asyncio.all_tasks() if task is not current and not task.done()]


async def test_an_early_prepare_runs_beside_the_first_model_call_and_its_first_call_takes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = early_loop(tmp_path)
    timed = Timed(loop, monkeypatch)
    agent_session_id = await loop.start(EARLY.name)
    await loop.say(agent_session_id, "Find the total.")
    loop.anthropic.add(reply(use("lookup")), reply(said("It is 12.")))
    before = unused(EARLY)

    done = await loop.loops.run(loop.owner, agent_session_id)

    assert done.outcome is LoopOutcome.SUCCEEDED
    assert timed.events == ["prepare", "answered", "prepared", "ran", "answered"], (
        "the prepare began before the first model call answered, and the call waited for it"
    )
    assert timed.prepares == [None], "one prepare in all"
    assert timed.live_when_ran == [True], "the call ran in the instance it made"
    assert agent_session_id not in timed.twin.live and timed.released == 1, "let go at the end"
    assert unused(EARLY) == before, "a prepare a call took is not counted"


async def test_a_kind_that_declares_nothing_prepares_at_its_first_call_and_never_without_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = early_loop(tmp_path)
    timed = Timed(loop, monkeypatch)
    agent_session_id = await loop.start(LATE.name)
    await loop.say(agent_session_id, "Find the total.")
    loop.anthropic.add(reply(use("lookup")), reply(said("It is 12.")))

    done = await loop.loops.run(loop.owner, agent_session_id)

    assert done.outcome is LoopOutcome.SUCCEEDED
    assert timed.events == ["answered", "prepare", "prepared", "ran", "answered"], (
        "prepared once the model asked for the call that reaches it"
    )

    timed.events.clear()
    await loop.say(agent_session_id, "What does the ledger say?")
    loop.anthropic.add(reply(call("ledger", q="the total")), reply(said("It is 12.")))
    again = await loop.loops.run(loop.owner, agent_session_id)

    assert again.outcome is LoopOutcome.SUCCEEDED
    assert timed.events == ["answered", "answered"] and timed.prepares == [None], (
        "a run that reaches no workspace prepares none"
    )
    assert unused(LATE) == 0


def gated(raised: OmException) -> Callable[[Managers, Clock], Parking]:
    """The budgets' gate, behind one that raises `raised` at a model call's
    hold."""
    return lambda managers, clock: Parking(managers, clock, turn=raised)


def over_budget() -> SpendBudgetRefused:
    """The budgets' refusal of a call its request's own amount cannot hold."""
    breach = SpendBreach(
        spend_budget_id=None,
        scope=None,
        unit=AmountUnit.COST,
        amount=0,
        committed=0,
        held=0,
        exposure=1,
        action=SpendBreachAction.RAISE,
        needed=1,
        resets_at=None,
    )
    return SpendBudgetRefused(Refusal(breaches=(breach,)))


STOPS = ["outage", "budget-refused", "gate-parked", "no-credential"]
"""What parks a run before its model call goes out."""


@pytest.mark.parametrize("stop", STOPS)
async def test_a_run_that_parks_before_its_model_call_goes_out_starts_no_prepare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stop: str
) -> None:
    """A provider marked failing, the gate's refusal or park, and a key the
    tenant does not hold each park the run before its call goes out. None
    starts a prepare, so the park, and each retry of it, spends nothing."""
    gate = {
        "budget-refused": gated(over_budget()),
        "gate-parked": gated(GateParked(NORM, "far above the session's norm")),
    }.get(stop)
    keyless = LanguageModelsLayer(credentials=lambda providers: Keyless())
    loop = early_loop(
        tmp_path,
        call_gate=gate,
        language_models_layer=keyless if stop == "no-credential" else None,
    )
    if stop == "outage":
        failing = Outage(
            org_id=SYSTEM_SCOPE,
            provider="anthropic",
            credential=loop.credential,
            retry_at=loop.clock.now + timedelta(minutes=1),
        )
        await loop.outages.mark(failing)
    timed = Timed(loop, monkeypatch)
    agent_session_id = await loop.start(EARLY.name)
    await loop.say(agent_session_id, "Find the total.")
    loop.anthropic.add(reply(use("lookup")))
    before = unused(EARLY)

    parked = await loop.loops.run(loop.owner, agent_session_id)

    assert parked.end is RunEnd.PARKED
    history = await loop.history(agent_session_id)
    assert of_type(history, AgentStepType.MODEL_REQUEST) == [], "no call went out"
    assert timed.prepares == [] and timed.released == 0, "and no prepare started"
    assert unused(EARLY) == before and others() == []


async def test_an_early_prepare_no_call_takes_is_let_go_unsnapshotted_and_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = early_loop(tmp_path)
    timed = Timed(loop, monkeypatch)
    agent_session_id = await loop.start(EARLY.name)
    await loop.say(agent_session_id, "What does the ledger say?")
    loop.anthropic.add(reply(call("ledger", q="the total")), reply(said("It is 12.")))
    before = unused(EARLY)

    done = await loop.loops.run(loop.owner, agent_session_id)

    assert done.outcome is LoopOutcome.SUCCEEDED
    assert timed.prepares == [None] and "prepared" in timed.events
    assert agent_session_id not in timed.twin.live and timed.released == 1, "its end let it go"
    assert timed.snapshots == 0
    assert unused(EARLY) == before + 1, "counted, by its kind"
    assert others() == [], "no prepare outlives its run"


async def test_an_early_prepare_of_a_kept_workspace_starts_from_its_snapshot_and_takes_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workspace kept by snapshots, untouched since its latest, is
    prepared early from it. A run that reaches it not lets it go with no
    snapshot: the latest still holds it whole."""
    loop = early_loop(tmp_path)
    timed = Timed(loop, monkeypatch)
    agent_session_id = await loop.start(EARLY_KEPT.name)
    await loop.say(agent_session_id, "Find the total.")
    loop.anthropic.add(reply(use("lookup")), reply(said("It is 12.")))
    timed.twin.files[agent_session_id] = b"as the run left it"
    assert (await loop.loops.run(loop.owner, agent_session_id)).outcome is LoopOutcome.SUCCEEDED
    assert timed.snapshots == 1 and agent_session_id not in timed.twin.live
    before = unused(EARLY_KEPT)

    await loop.say(agent_session_id, "What does the ledger say?")
    loop.anthropic.add(reply(call("ledger", q="the total")), reply(said("It is 12.")))
    done = await loop.loops.run(loop.owner, agent_session_id)

    assert done.outcome is LoopOutcome.SUCCEEDED
    assert timed.prepares == [None, b"as the run left it"], "prepared early, from the latest"
    assert timed.snapshots == 1, "no snapshot of a workspace no call touched"
    history = await loop.history(agent_session_id)
    assert len(of_type(history, AgentStepType.SNAPSHOTTED)) == 1
    assert agent_session_id not in timed.twin.live and timed.released == 2
    assert unused(EARLY_KEPT) == before + 1


async def test_an_early_prepare_from_a_snapshot_a_restore_replaced_since_is_let_go(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A principal asks for a restore while the run's first model call runs.
    The early prepare started from the latest snapshot, so its instance goes,
    and the first call prepares from the snapshot the restore names."""
    loop = early_loop(tmp_path)
    timed = Timed(loop, monkeypatch)
    agent_session_id = await loop.start(EARLY_KEPT.name)
    for files in (b"first", b"second"):
        await loop.say(agent_session_id, "Find the total.")
        loop.anthropic.add(reply(use("lookup")), reply(said("It is 12.")))
        timed.writes = files
        assert (await loop.loops.run(loop.owner, agent_session_id)).outcome is LoopOutcome.SUCCEEDED
    first = of_type(await loop.history(agent_session_id), AgentStepType.SNAPSHOTTED)[0]
    assert isinstance(first.header, SnapshotHeader)
    released = timed.released
    timed.writes = None
    await loop.say(agent_session_id, "Go on from the first state.")
    loop.anthropic.add(reply(use("lookup")), reply(said("It is 12.")))
    timed.began.clear()
    timed.hold = asyncio.Event()
    running = asyncio.ensure_future(loop.loops.run(loop.owner, agent_session_id))
    await timed.began.wait()
    snapshot = await loop.managers.tools.find_snapshot(
        loop.owner, agent_session_id, first.header.snapshot.id
    )
    restore = control_step(
        new_id(),
        loop.clock.now,
        agent_session_id,
        loop.owner,
        ControlCommand.RESTORE,
        snapshot=snapshot,
    )
    await loop.managers.agent_sessions.receive(loop.owner, agent_session_id, [restore])
    timed.hold.set()

    done = await running

    assert done.outcome is LoopOutcome.SUCCEEDED
    assert timed.prepares[-2:] == [b"second", b"first"], "early from the latest, then restored"
    assert timed.twin.files[agent_session_id] == b"first" and timed.live_when_ran[-1]
    assert timed.released == released + 2, "the early instance went, then the run's at its end"


async def test_a_kept_workspace_touched_since_its_latest_snapshot_is_never_prepared_early(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run whose snapshot failed keeps its instance live, holding what no
    snapshot holds. The next run prepares nothing early, so no end lets that
    instance go: its first call finds it as it stands."""
    loop = early_loop(tmp_path)
    timed = Timed(loop, monkeypatch)
    agent_session_id = await loop.start(EARLY_KEPT.name)
    await loop.say(agent_session_id, "Find the total.")
    loop.anthropic.add(reply(use("lookup")), reply(said("It is 12.")))

    async def failed(*args: object, **kwargs: object) -> AgentStep:
        raise RuntimeError("the store is down")

    with monkeypatch.context() as patched:
        patched.setattr(loop.managers.tools, "snapshot_agent_workspace", failed)
        assert (await loop.loops.run(loop.owner, agent_session_id)).outcome is LoopOutcome.SUCCEEDED
    assert agent_session_id in timed.twin.live, "no snapshot holds it, so it stays"

    timed.events.clear()
    await loop.say(agent_session_id, "What does the ledger say?")
    loop.anthropic.add(reply(call("ledger", q="the total")), reply(said("It is 12.")))
    done = await loop.loops.run(loop.owner, agent_session_id)

    assert done.outcome is LoopOutcome.SUCCEEDED
    assert timed.events == ["answered", "answered"] and timed.prepares == [None]
    assert agent_session_id in timed.twin.live and timed.released == 0, "it still stands"


async def test_an_early_prepare_that_fails_leaves_the_first_call_to_prepare_as_it_would(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refusal that clears, such as a machine still starting: the first
    call prepares again, and finds what the early prepare started."""
    loop = early_loop(tmp_path)
    timed = Timed(loop, monkeypatch)
    timed.failing.append(
        IsolationRefused("the machine is still starting", clears=True, cause="starting")
    )
    agent_session_id = await loop.start(EARLY.name)
    await loop.say(agent_session_id, "Find the total.")
    loop.anthropic.add(reply(use("lookup")), reply(said("It is 12.")))

    done = await loop.loops.run(loop.owner, agent_session_id)

    assert done.outcome is LoopOutcome.SUCCEEDED
    assert timed.events == [
        "prepare",
        "answered",
        "refused",
        "prepare",
        "prepared",
        "ran",
        "answered",
    ], "the call prepared again once the early prepare failed"
    assert timed.live_when_ran == [True] and agent_session_id not in timed.twin.live


async def test_an_early_refusal_that_stands_reaches_the_first_call_as_a_prepares_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = early_loop(tmp_path)
    timed = Timed(loop, monkeypatch)
    cause = "the base's setup command 1, 'make', exited 3"
    timed.failing.extend(IsolationRefused("setup failed", cause=cause) for _ in range(2))
    agent_session_id = await loop.start(EARLY.name)
    await loop.say(agent_session_id, "Find the total.")
    loop.anthropic.add(reply(use("lookup")))

    ended = await loop.loops.run(loop.owner, agent_session_id)

    assert ended.outcome is LoopOutcome.ERRORED
    last = (await loop.history(agent_session_id))[-1]
    assert isinstance(last.header, LoopEndedHeader) and last.header.cause == cause
    assert len(timed.prepares) == 2 and "ran" not in timed.events
    assert others() == []


async def test_a_run_cancelled_by_its_person_lets_its_early_prepare_finish_and_go(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = early_loop(tmp_path)
    timed = Timed(loop, monkeypatch)
    agent_session_id = await loop.start(EARLY.name)
    await loop.say(agent_session_id, "Find the total.")
    loop.anthropic.add(reply(use("lookup")))
    before = unused(EARLY)
    running = asyncio.ensure_future(loop.loops.run(loop.owner, agent_session_id))
    await timed.began.wait()
    cancel = control(loop, agent_session_id, ControlCommand.CANCEL)
    await loop.managers.agent_steps.append_inputs(loop.owner, agent_session_id, [cancel])

    run = await running

    assert run.outcome is LoopOutcome.CANCELLED and "ran" not in timed.events
    assert others() == [], "no prepare outlives its run"
    assert agent_session_id not in timed.twin.live and unused(EARLY) == before + 1


async def test_a_run_whose_task_is_cancelled_cancels_its_early_prepare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = early_loop(tmp_path)
    timed = Timed(loop, monkeypatch)
    booting = asyncio.Event()
    stopped: list[bool] = []

    async def never(*args: object, **kwargs: object) -> AgentWorkspace:
        booting.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            stopped.append(True)
            raise
        raise AssertionError("a prepare that never ends")

    monkeypatch.setattr(timed.twin, "prepare", never)
    agent_session_id = await loop.start(EARLY.name)
    await loop.say(agent_session_id, "Find the total.")
    loop.anthropic.add(reply(use("lookup")))
    running = asyncio.ensure_future(loop.loops.run(loop.owner, agent_session_id))
    await booting.wait()

    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    assert stopped == [True], "its prepare was cancelled with it"
    assert others() == [], "and no task outlives the run"
    assert agent_session_id not in timed.twin.live


def test_a_kind_with_no_workspace_never_prepares_early() -> None:
    with pytest.raises(ValidationError, match="prepares its workspace early works in one"):
        AgentKind.model_validate({**ASSISTANT.model_dump(), "prepares_early": True})
