"""A session's workspace, over the memory storage, the scripted provider,
and the twin workspace provider (ADR 1009, ADR 1043). Before a loop's
first model call the provider checks the session's spec alone, and makes
nothing: a spec it can never meet ends the loop `errored`, its cause
named, before any call, and its end report wakes a parent waiting on it.
The workspace of a kind that declares nothing is prepared at the first
call that reaches it, after the model call that asked for it, and a
loop whose calls reach none prepares none; a kind that prepares early is
`test_loop_early_workspaces`'s. A prepare that waits for a workspace that may come parks the loop
on `leasable_resource` with the call open, and the run that takes it up
again runs the call once; a workspace whose durable state is gone parks
it for a person. A park at a call its own run wrote is settled; one
written while a lost run's calls are settled is not."""

from pathlib import Path
from uuid import UUID

import pytest

from acme.infra.agent_workspaces import (
    AgentWorkspace,
    AgentWorkspaceLost,
    AgentWorkspaceProviderInterface,
    EgressMode,
    EgressPolicy,
    IsolationMode,
    IsolationRefused,
    IsolationSpec,
)
from acme.infra.agent_workspaces.twin import AgentWorkspaceNullImpl, AgentWorkspaceTwinImpl
from acme.infra.impl.local import InfraLocalImpl
from acme.om.agent_sessions.types.agent_session import AgentSessionStatus
from acme.om.agent_steps.types.agent_step import AgentStepType
from acme.om.agent_steps.types.header import (
    LoopEndedHeader,
    LoopOutcome,
    ParkedHeader,
    ParkReason,
    ToolFailure,
    ToolRequestHeader,
)
from acme.om.agents.impl.loop import LoopOptions
from acme.om.agents.impl.loop_workspace import REFUSED
from acme.om.agents.rules import CHILDREN_PARK
from acme.om.agents.types.run import RunEnd
from acme.om.context import TenantContext
from acme.om.tools.native.wait_for_sub_agents import WAIT_FOR_SUB_AGENTS

from contracts import sub_agents
from contracts.loops import (
    ASSISTANT,
    DELIVERY,
    Lookup,
    Loop,
    answer_to,
    call,
    loop_over,
    of_type,
    reply,
    said,
    text_of,
    use,
)
from contracts.sub_agents import failure_of
from contracts.sub_agents import text_of as said_by

TWIN = IsolationSpec(mode=IsolationMode.TWIN, egress=EgressPolicy(mode=EgressMode.NONE))
CONTAINER = IsolationSpec(mode=IsolationMode.CONTAINER, egress=EgressPolicy(mode=EgressMode.NONE))
"""A spec neither the twin nor the null provider makes."""

HANDS = ASSISTANT.model_copy(
    update={"name": "hands", "isolation": TWIN, "tools": ("lookup", "ledger")}
)
"""A kind that works in a workspace: `lookup` reaches it, `ledger` does not."""
BOXED = HANDS.model_copy(update={"name": "boxed", "isolation": CONTAINER})
"""A kind whose spec the twin can never meet."""


class Recorded:
    """The twin provider's calls as the loop makes them: how many checks,
    and for each prepare, how many model requests its session's history
    held as it came."""

    def __init__(self, loop: Loop, monkeypatch: pytest.MonkeyPatch) -> None:
        twin = loop.infra.get_agent_workspaces()
        assert isinstance(twin, AgentWorkspaceTwinImpl)
        self.twin = twin
        self.checks = 0
        self.prepares: list[int] = []
        check, prepare = twin.check, twin.prepare

        async def checked(spec: IsolationSpec) -> None:
            self.checks += 1
            await check(spec)

        async def prepared(
            org_id: UUID,
            agent_workspace_id: UUID,
            spec: IsolationSpec,
            snapshot: bytes | None = None,
            base: bytes | None = None,
            *,
            building: bool = False,
        ) -> AgentWorkspace:
            held = of_type(await loop.history(agent_workspace_id), AgentStepType.MODEL_REQUEST)
            self.prepares.append(len(held))
            return await prepare(
                org_id, agent_workspace_id, spec, snapshot, base, building=building
            )

        monkeypatch.setattr(twin, "check", checked)
        monkeypatch.setattr(twin, "prepare", prepared)


def hands_loop(tmp_path: Path) -> Loop:
    return loop_over(
        tmp_path,
        kinds=(ASSISTANT, DELIVERY, HANDS, BOXED),
        infra=InfraLocalImpl(tmp_path),
        extra=(Lookup("ledger", workspace=False),),
    )


async def test_the_first_model_call_is_kept_before_the_workspace_is_prepared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = hands_loop(tmp_path)
    recorded = Recorded(loop, monkeypatch)
    agent_session_id = await loop.start(HANDS.name)
    await loop.say(agent_session_id, "Find the total.")
    loop.anthropic.add(reply(use("lookup")), reply(said("It is 12.")))

    done = await loop.loops.run(loop.owner, agent_session_id)

    assert done.outcome is LoopOutcome.SUCCEEDED
    assert recorded.checks == 1, "the spec is checked once, before the first call"
    assert recorded.prepares == [1], "prepared once, after the first request was kept"
    (request,) = of_type(await loop.history(agent_session_id), AgentStepType.TOOL_REQUEST)
    assert isinstance(request.header, ToolRequestHeader) and request.header.workspace
    assert agent_session_id not in recorded.twin.live, "its run let the instance go"


async def test_a_loop_whose_calls_reach_no_workspace_prepares_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = hands_loop(tmp_path)
    recorded = Recorded(loop, monkeypatch)
    agent_session_id = await loop.start(HANDS.name)
    await loop.say(agent_session_id, "What does the ledger say?")
    loop.anthropic.add(reply(call("ledger", q="the total")), reply(said("It is 12.")))

    done = await loop.loops.run(loop.owner, agent_session_id)

    assert done.outcome is LoopOutcome.SUCCEEDED
    assert (recorded.checks, recorded.prepares) == (1, []), "checked, and never prepared"
    (request,) = of_type(await loop.history(agent_session_id), AgentStepType.TOOL_REQUEST)
    assert isinstance(request.header, ToolRequestHeader) and not request.header.workspace
    assert "ledger found the total" in text_of(loop.anthropic.calls[-1]), "it ran with none"


async def test_a_spec_the_provider_never_meets_ends_the_loop_before_any_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = hands_loop(tmp_path)
    recorded = Recorded(loop, monkeypatch)
    agent_session_id = await loop.start(BOXED.name)
    await loop.say(agent_session_id, "Find the total.")
    loop.anthropic.add(reply(use("lookup")), reply(said("It is 12.")))

    run = await loop.loops.run(loop.owner, agent_session_id)

    assert (run.end, run.outcome, run.park) == (RunEnd.ENDED, LoopOutcome.ERRORED, None)
    with pytest.raises(IsolationRefused) as expected:
        await recorded.twin.check(CONTAINER)
    history = await loop.history(agent_session_id)
    ended = history[-1]
    assert isinstance(ended.header, LoopEndedHeader) and expected.value.cause is not None
    assert ended.header.cause == expected.value.cause, "its end says what the spec asks"
    assert of_type(history, AgentStepType.MODEL_REQUEST) == [], "no model call was spent"
    assert loop.anthropic.calls == [] and recorded.prepares == [], "nothing was made"


@pytest.mark.parametrize(
    "provider", [AgentWorkspaceNullImpl(), AgentWorkspaceTwinImpl()], ids=lambda p: p.describe()
)
async def test_a_refused_spec_ends_the_loop_errored_and_wakes_its_waiting_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: AgentWorkspaceProviderInterface
) -> None:
    loop = loop_over(tmp_path, kinds=sub_agents.KINDS)
    root = await sub_agents.a_root(loop, "What is the quarterly total?")
    loop.anthropic.add(reply(sub_agents.spawn("the total")), reply(call(WAIT_FOR_SUB_AGENTS)))
    waiting = await loop.loops.run(loop.owner, root)
    assert waiting.park == CHILDREN_PARK
    (child,) = await sub_agents.children_of(loop, root)
    calls = len(loop.anthropic.calls)

    async def refused(ctx: TenantContext, spec: IsolationSpec) -> None:
        await provider.check(CONTAINER)

    monkeypatch.setattr(loop.managers.tools, "check_agent_workspace", refused)
    run = await loop.loops.run(loop.owner, child.id)

    assert (run.end, run.outcome, run.park) == (RunEnd.ENDED, LoopOutcome.ERRORED, None)
    with pytest.raises(IsolationRefused) as expected:
        await provider.check(CONTAINER)
    history = await loop.history(child.id)
    ended = history[-1]
    assert isinstance(ended.header, LoopEndedHeader) and expected.value.cause is not None
    assert ended.header.cause == expected.value.cause, "its end says what the spec asks"
    assert of_type(history, AgentStepType.MODEL_REQUEST) == []
    assert len(loop.anthropic.calls) == calls, "no call was made"
    woken = await loop.managers.agent_sessions.get_agent_session(loop.owner, root)
    assert (woken.status, woken.park) == (AgentSessionStatus.PENDING, None), "its report woke it"


@pytest.mark.parametrize(
    "refusal",
    [
        IsolationRefused("setup command 1 exited 3", cause="the base's setup command 1 exited 3"),
        IsolationRefused("a container workspace needs Docker: the daemon is not running"),
    ],
    ids=["setup", "engine"],
)
async def test_a_refusal_that_cannot_clear_at_the_first_call_answers_every_call_left_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, refusal: IsolationRefused
) -> None:
    """A base whose setup fails, or a provider that cannot reach its engine,
    is learned at the first call that reaches the workspace. The loop ends
    `errored`, and each call the turn left open is answered first, never
    run, with the refusal's cause: the session's next input starts a loop
    that calls the model, and the model reads why."""
    loop = hands_loop(tmp_path)
    twin = loop.infra.get_agent_workspaces()
    agent_session_id = await loop.start(HANDS.name)
    await loop.say(agent_session_id, "Find the total, and check the ledger.")
    loop.anthropic.add(reply(use("lookup"), call("ledger", q="the total")))

    async def refused(*args: object, **kwargs: object) -> AgentWorkspace:
        raise refusal

    with monkeypatch.context() as patched:
        patched.setattr(twin, "prepare", refused)
        ended = await loop.loops.run(loop.owner, agent_session_id)

    assert (ended.end, ended.outcome) == (RunEnd.ENDED, LoopOutcome.ERRORED)
    history = await loop.history(agent_session_id)
    last = history[-1]
    assert isinstance(last.header, LoopEndedHeader) and last.header.cause == refusal.cause
    requests = of_type(history, AgentStepType.TOOL_REQUEST)
    assert len(requests) == 2
    why = REFUSED if refusal.cause is None else f"{REFUSED}: {refusal.cause}"
    for request in requests:
        answer = answer_to(history, request)
        assert failure_of(answer) is ToolFailure.PERMANENT
        assert f"not run: {why}" in said_by(answer), "never run, and why"
    assert loop.tools["lookup"].ran_as == [], "nothing ran"

    await loop.say(agent_session_id, "The setup is fixed. Try again.")
    loop.anthropic.add(reply(said("It is 12.")))
    again = await loop.loops.run(loop.owner, agent_session_id)

    assert again.outcome is LoopOutcome.SUCCEEDED, "the next input started a loop"
    assert len(loop.anthropic.calls) == 2, "and that loop called the model"
    assert why in text_of(loop.anthropic.calls[-1]), "the model read why its calls never ran"


async def test_a_spec_refused_on_a_resumed_run_answers_the_call_its_park_held_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that resumes a park holding a call open, and finds the spec
    refused, answers that call with the cause before the loop ends."""
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "Find the total and send it.")
    loop.anthropic.add(reply(use("lookup", use_id="use_lookup")), reply(use("send")))
    asked = await loop.loops.run(loop.owner, agent_session_id)
    assert asked.park is not None and asked.park.unlock == "approval"
    (send,) = [
        step
        for step in await loop.history(agent_session_id)
        if isinstance(step.header, ToolRequestHeader) and step.header.tool == "send"
    ]
    await loop.managers.tools.decide_call(loop.owner, agent_session_id, send.seq, approve=True)

    async def never(ctx: TenantContext, spec: IsolationSpec) -> None:
        await loop.infra.get_agent_workspaces().check(CONTAINER)

    monkeypatch.setattr(loop.managers.tools, "check_agent_workspace", never)
    ended = await loop.loops.run(loop.owner, agent_session_id)

    assert (ended.end, ended.outcome) == (RunEnd.ENDED, LoopOutcome.ERRORED)
    history = await loop.history(agent_session_id)
    last = history[-1]
    assert isinstance(last.header, LoopEndedHeader) and last.header.cause is not None
    answer = answer_to(history, send)
    assert failure_of(answer) is ToolFailure.PERMANENT, "its park held it back: it never ran"
    assert last.header.cause in said_by(answer), "the call held open reads the cause"
    assert loop.tools["send"].ran_as == []


async def test_a_workspace_that_must_wait_parks_with_its_call_open_and_runs_it_once_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = hands_loop(tmp_path)
    twin = loop.infra.get_agent_workspaces()
    agent_session_id = await loop.start(HANDS.name)
    await loop.say(agent_session_id, "What is the total?")
    loop.anthropic.add(reply(use("lookup", use_id="use_lookup")))

    async def refused(*args: object, **kwargs: object) -> AgentWorkspace:
        raise IsolationRefused(
            "host-7 holds every workspace", clears=True, cause="no workspace is free yet"
        )

    with monkeypatch.context() as patched:
        patched.setattr(twin, "prepare", refused)
        waiting = await loop.loops.run(loop.owner, agent_session_id)

    assert waiting.end is RunEnd.PARKED and waiting.park is not None, "never ended"
    assert (waiting.park.reason, waiting.park.unlock, waiting.park.retry_at) == (
        ParkReason.LEASABLE_RESOURCE,
        "agent_workspace",
        loop.clock() + LoopOptions().agent_workspace_wait,
    )
    assert waiting.park.cause == "no workspace is free yet", "its cause, never its message"
    assert not waiting.park.unsettled, "the call it holds open never ran"
    history = await loop.history(agent_session_id)
    assert len(of_type(history, AgentStepType.TOOL_REQUEST)) == 1
    assert of_type(history, AgentStepType.TOOL_RESPONSE) == [], "the call waits, open"
    assert len(loop.anthropic.calls) == 1, "the model was called; only the call waits"
    assert loop.tools["lookup"].ran_as == []

    assert waiting.park.retry_at is not None
    loop.clock.now = waiting.park.retry_at
    await loop.managers.agent_sessions.wake_agent_session(
        loop.owner, agent_session_id, waiting.park
    )
    loop.anthropic.add(reply(said("The total is 12.")))
    done = await loop.loops.run(loop.owner, agent_session_id)

    assert done.outcome is LoopOutcome.SUCCEEDED, "the workspace came, and the loop went on"
    assert loop.tools["lookup"].ran_as == [loop.owner.user_id], "the call ran once"
    history = await loop.history(agent_session_id)
    assert len(of_type(history, AgentStepType.TOOL_RESPONSE)) == 1
    assert "lookup found the total" in text_of(loop.anthropic.calls[-1]), "its result reached it"


async def test_a_lost_workspace_parks_for_a_person_at_the_call_that_needs_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "What is the total?")
    loop.anthropic.add(reply(use("lookup", use_id="use_lookup")), reply(said("Never asked.")))

    async def lost(*args: object, **kwargs: object) -> AgentWorkspace:
        raise AgentWorkspaceLost("what the workspace is rebuilt from is gone")

    monkeypatch.setattr(loop.managers.tools, "prepare_agent_workspace", lost)
    run = await loop.loops.run(loop.owner, agent_session_id)

    assert run.end is RunEnd.PARKED and run.park is not None, "never ended"
    assert (run.park.reason, run.park.unlock, run.park.retry_at) == (
        ParkReason.PERSON,
        "agent_workspace",
        None,
    )
    assert not run.park.unsettled, "its run wrote the call it holds open"
    parked = (await loop.history(agent_session_id))[-1]
    assert isinstance(parked.header, ParkedHeader) and not parked.header.park.unsettled
    assert len(loop.anthropic.calls) == 1 and loop.tools["lookup"].ran_as == []


async def test_a_refused_isolation_after_a_settled_park_parks_on_the_resource_settled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = loop_over(tmp_path)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "Find the total and send it.")
    loop.anthropic.add(reply(use("lookup", use_id="use_lookup")), reply(use("send")))
    asked = await loop.loops.run(loop.owner, agent_session_id)
    assert asked.park is not None and asked.park.unlock == "approval"
    assert not asked.park.unsettled, "the loop wrote it at a safe point"
    (send,) = [
        step
        for step in await loop.history(agent_session_id)
        if isinstance(step.header, ToolRequestHeader) and step.header.tool == "send"
    ]
    await loop.managers.tools.decide_call(loop.owner, agent_session_id, send.seq, approve=True)

    async def refused(*args: object, **kwargs: object) -> AgentWorkspace:
        raise IsolationRefused("no host can give it the workspace yet", clears=True)

    monkeypatch.setattr(loop.managers.tools, "prepare_agent_workspace", refused)
    waiting = await loop.loops.run(loop.owner, agent_session_id)

    assert waiting.end is RunEnd.PARKED and waiting.park is not None
    assert (waiting.park.reason, waiting.park.unlock) == (
        ParkReason.LEASABLE_RESOURCE,
        "agent_workspace",
    )
    assert waiting.park.retry_at is not None and waiting.park.retry_at > loop.clock()
    assert not waiting.park.unsettled, "its run resumed a settled park"
    assert loop.tools["send"].ran_as == [], "nothing ran"
    assert [s.type for s in await loop.history(agent_session_id)][-1] is AgentStepType.PARKED
