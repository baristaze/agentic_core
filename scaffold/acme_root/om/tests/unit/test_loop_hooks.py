"""A layer above the engine extends a run through the loop's hooks alone,
with no edit to the loop: the toolsets it attaches to a session, held and
deferred, and what it does before a run's first call, a renewal it runs
between loops and a step that tells the model what changed under it. The
layer here is such a layer's hooks, as it would write them. What it
attaches holds every guard a kind's own tools hold: who may instruct the
session, the rule of two a child's report carries, and the secrets a
snapshot is scanned for."""

import asyncio
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest

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
from acme.infra.outages import Outage
from acme.infra.transports import SecretUse, SecretVia
from acme.integrations.model_providers.calls import ModelCall
from acme.om.agent_sessions.types.agent_session import AgentSession
from acme.om.agent_steps.types.agent_step import Actor, AgentStep, AgentStepType, Origin
from acme.om.agent_steps.types.content import Content, TextBlock, ToolResultBlock, ToolUseBlock
from acme.om.agent_steps.types.header import (
    ControlCommand,
    LoopEndedHeader,
    LoopOutcome,
    MarkHeader,
    ParkReason,
    SnapshotHeader,
)
from acme.om.agents.hooks import (
    NO_ATTACHED_TOOLSETS,
    AttachedToolsets,
    LoopHooksInterface,
    LoopStart,
    with_attached_toolsets,
)
from acme.om.agents.types.request import Spawn
from acme.om.agents.types.run import LoopRun, RunEnd
from acme.om.base import OmBase, new_id, utcnow
from acme.om.context import Role, TenantContext
from acme.om.exceptions import NotAuthorized, UnresolvedRole
from acme.om.tools.rules import DEFAULT_CEILINGS
from acme.om.tools.tool import ToolRuntime
from acme.om.tools.types.policy import ToolPolicyLayer
from acme.om.tools.types.tool import Effect, ToolClass, ToolInput, ToolSpec
from acme.om.tools.types.toolset import LOAD_TOOLSET, Toolset

from contracts.doubles import context
from contracts.factories import make_org
from contracts.loops import (
    ASSISTANT,
    DELIVERY,
    LEAD,
    WORKER,
    Asked,
    Found,
    Lookup,
    call,
    control,
    loop_over,
    reply,
    said,
    text_of,
    use,
)

LEDGER = Toolset(name="ledger", version=1, summary="The tenant's ledger.", tools=("ledger_read",))
ARCHIVE = Toolset(
    name="archive", version=1, summary="The tenant's archive.", tools=("archive_search",)
)
CHANGED = "the files under the workspace were rebuilt since your last loop"
REBUILT = "the workspace was rebuilt from its base since your last loop"
SETTINGS = Toolset(
    name="settings", version=1, summary="The tenant's settings.", tools=("settings_write",)
)
VAULT = Toolset(name="vault", version=1, summary="The tenant's vault.", tools=("vault_sync",))
TOKEN, VALUE = "VAULT_TOKEN", "tok-0123456789abcdef"
KEEPER = ASSISTANT.model_copy(
    update={
        "name": "keeper",
        "isolation": IsolationSpec(
            mode=IsolationMode.TWIN,
            egress=EgressPolicy(mode=EgressMode.NONE),
            durability=Durability.SNAPSHOT,
        ),
    }
)


class VaultSync(Lookup):
    """A tool a layer attaches, given `TOKEN` injected into its command. On
    the twin it leaves the secret's value in the workspace's files, as a
    command that echoes its variables would. No tool of the catalog is
    given `TOKEN`."""

    def __init__(self, twin: AgentWorkspaceTwinImpl | None = None) -> None:
        super().__init__(
            "vault_sync", effect=Effect.IDEMPOTENT, authorization_class=ToolClass.WRITE
        )
        injected = SecretUse(name=TOKEN, via=SecretVia.INJECTED, env=TOKEN)
        self._given = super().spec.model_copy(update={"secrets": (injected,)})
        self._twin = twin

    @property
    def spec(self) -> ToolSpec:
        return self._given

    async def run(self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime) -> OmBase:
        assert isinstance(call_input, Asked)
        if self._twin is not None:
            self._twin.files[runtime.agent_session_id] = f"token={VALUE}".encode()
        return Found(text="synced")


def notice(start: LoopStart, text: str) -> AgentStep:
    """A layer's notice that something changed under the model."""
    return AgentStep(
        id=new_id(),
        created_at=utcnow(),
        agent_session_id=start.agent_session.id,
        loop_id=start.loop_id,
        type=AgentStepType.ENVIRONMENT_CHANGED,
        actor=Actor.ENGINE,
        origin=Origin.ENGINE,
        header=MarkHeader(),
        content=Content(blocks=(TextBlock(text=text),)),
    )


class Layer(LoopHooksInterface):
    """A layer's hooks: what it attaches to every session, a renewal it runs
    at a new loop under the run's epoch, a step it appends before each
    run's first call when something changed, and one it appends once the
    run's workspace is prepared when something changed in it. `refuses`
    stands for a renewal that finds no fill for the session, and `refusal`
    for a session whose workspace the layer knows cannot be had."""

    def __init__(
        self,
        attached: AttachedToolsets = NO_ATTACHED_TOOLSETS,
        *,
        changed: str | None = None,
        rebuilt: str | None = None,
        refuses: bool = False,
        refusal: IsolationRefused | None = None,
    ) -> None:
        self.attached = attached
        self.changed = changed
        self.rebuilt = rebuilt
        self.refuses = refuses
        self.refusal = refusal
        self.asked: list[UUID] = []
        self.starts: list[LoopStart] = []
        self.renewed: list[tuple[UUID, int]] = []
        self.prepared: list[tuple[LoopStart, AgentWorkspace]] = []

    async def attached_toolsets(
        self, ctx: TenantContext, agent_session: AgentSession
    ) -> AttachedToolsets:
        self.asked.append(agent_session.id)
        return self.attached

    async def before_first_call(self, ctx: TenantContext, start: LoopStart) -> Sequence[AgentStep]:
        self.starts.append(start)
        if self.refusal is not None:
            raise self.refusal
        if start.new_loop:
            if self.refuses:
                raise UnresolvedRole("no fill serves the main role any longer")
            self.renewed.append((start.loop_id, start.epoch))
        return () if self.changed is None else (notice(start, self.changed),)

    async def workspace_prepared(
        self, ctx: TenantContext, start: LoopStart, workspace: AgentWorkspace
    ) -> Sequence[AgentStep]:
        self.prepared.append((start, workspace))
        return () if self.rebuilt is None else (notice(start, self.rebuilt),)


class ChildLayer(Layer):
    """A layer that attaches its toolsets to a child alone."""

    async def attached_toolsets(
        self, ctx: TenantContext, agent_session: AgentSession
    ) -> AttachedToolsets:
        if agent_session.parent_id is None:
            return NO_ATTACHED_TOOLSETS
        return await super().attached_toolsets(ctx, agent_session)


def offered(call: ModelCall) -> list[str]:
    return sorted(tool.name for tool in call.tools)


def answers(steps: list[AgentStep]) -> list[str]:
    texts: list[str] = []
    for step in steps:
        if step.type is not AgentStepType.TOOL_RESPONSE or step.content is None:
            continue
        for block in step.content.blocks:
            parts = block.parts if isinstance(block, ToolResultBlock) else (block,)
            texts.extend(part.text for part in parts if isinstance(part, TextBlock))
    return texts


async def test_a_session_calls_its_attached_toolsets_held_from_the_start_and_loaded(
    tmp_path: Path,
) -> None:
    ledger, archive = Lookup("ledger_read"), Lookup("archive_search")
    layer = Layer(AttachedToolsets(held=(LEDGER,), deferred=(ARCHIVE,), tools=(ledger, archive)))
    loop = loop_over(tmp_path, hooks=layer)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "What do the ledger and the archive say?")
    load = ToolUseBlock(id="use_load", name=LOAD_TOOLSET, input={"toolset": "archive"})
    loop.anthropic.add(
        reply(use("ledger_read")),
        reply(load),
        reply(use("archive_search")),
        reply(said("Both say 12.")),
    )
    asked = len(layer.asked)

    ran = await loop.loops.run(loop.owner, agent_session_id)

    assert ran.outcome is LoopOutcome.SUCCEEDED
    first, loaded, searched, _ = loop.anthropic.calls
    assert {"ledger_read", LOAD_TOOLSET} <= set(offered(first)), "held from the start"
    assert "archive_search" not in offered(first), "a deferred toolset waits for its load"
    assert "archive_search" not in offered(loaded), "never for a call of the turn that loaded it"
    assert "archive_search" in offered(searched), "the next request offers what the load added"
    assert ledger.ran_as == [loop.owner.user_id] and archive.ran_as == [loop.owner.user_id]
    steps = await loop.history(agent_session_id)
    assert '{"text":"ledger_read found the total"}' in answers(steps)
    assert '{"text":"archive_search found the total"}' in answers(steps)
    assert [s.type for s in steps].count(AgentStepType.SWITCHED) == 1, "the load is recorded"
    assert layer.asked[asked:] == [agent_session_id], "asked once a run"


async def test_a_step_the_hooks_append_reaches_the_model_before_the_runs_first_call(
    tmp_path: Path,
) -> None:
    layer = Layer(changed=CHANGED)
    loop = loop_over(tmp_path, hooks=layer)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "Find the total.")
    loop.anthropic.add(reply(said("It is 12.")))

    ran = await loop.loops.run(loop.owner, agent_session_id)

    assert ran.outcome is LoopOutcome.SUCCEEDED
    types = [step.type for step in await loop.history(agent_session_id)]
    changed = types.index(AgentStepType.ENVIRONMENT_CHANGED)
    assert changed < types.index(AgentStepType.MODEL_REQUEST), "written before the first call"
    assert CHANGED in text_of(loop.anthropic.calls[0]), "and the first request carries it"
    (start,) = layer.starts
    assert start.new_loop and start.agent_session.id == agent_session_id
    assert layer.renewed == [(ran.loop_id, ran.epoch)], "renewed under the run's own epoch"


async def test_the_hooks_tell_a_new_loop_from_one_a_run_takes_up_again(tmp_path: Path) -> None:
    layer = Layer()
    loop = loop_over(tmp_path, hooks=layer)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "Find the total.")
    loop.anthropic.add(reply(use("slow", use_id="use_slow")), reply(said("It is 12.")))
    slow = loop.tools["slow"]
    running = asyncio.ensure_future(loop.loops.run(loop.owner, agent_session_id))
    await slow.started.wait()
    await loop.managers.agent_steps.append_inputs(
        loop.owner, agent_session_id, [control(loop, agent_session_id, ControlCommand.PAUSE)]
    )
    slow.release.set()
    paused = await running
    assert paused.park is not None and paused.park.reason is ParkReason.PAUSE
    await loop.managers.agent_steps.append_inputs(
        loop.owner, agent_session_id, [control(loop, agent_session_id, ControlCommand.RESUME)]
    )

    resumed = await loop.loops.run(loop.owner, agent_session_id)

    assert resumed.outcome is LoopOutcome.SUCCEEDED
    assert [start.new_loop for start in layer.starts] == [True, False]
    assert [start.epoch for start in layer.starts] == [paused.epoch, resumed.epoch]
    assert layer.renewed == [(paused.loop_id, paused.epoch)], "once, at the new loop"


async def test_a_loop_whose_first_run_parks_before_its_first_call_is_new_to_the_next(
    tmp_path: Path,
) -> None:
    layer = Layer()
    loop = loop_over(tmp_path, hooks=layer)
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "Find the total.")
    loop.anthropic.add(reply(said("It is 12.")))
    retry_at = loop.clock() + timedelta(minutes=1)
    await loop.outages.mark(
        Outage(
            org_id=SYSTEM_SCOPE, provider="anthropic", credential=loop.credential, retry_at=retry_at
        )
    )

    waiting = await loop.loops.run(loop.owner, agent_session_id)
    assert waiting.park is not None and waiting.park.reason is ParkReason.PROVIDER
    loop.clock.now = retry_at
    await loop.managers.agent_sessions.wake_agent_session(
        loop.owner, agent_session_id, waiting.park
    )
    done = await loop.loops.run(loop.owner, agent_session_id)

    assert done.outcome is LoopOutcome.SUCCEEDED and done.loop_id == waiting.loop_id
    assert [start.new_loop for start in layer.starts] == [True, True], "new until its first call"
    assert layer.renewed == [
        (waiting.loop_id, waiting.epoch),
        (done.loop_id, done.epoch),
    ], "renewed before that call, by each run that reached it"


async def test_a_renewal_that_finds_no_fill_ends_the_loop_before_any_call(tmp_path: Path) -> None:
    loop = loop_over(tmp_path, hooks=Layer(refuses=True))
    agent_session_id = await loop.start()
    await loop.say(agent_session_id, "Find the total.")
    loop.anthropic.add(reply(said("It is 12.")))

    ran = await loop.loops.run(loop.owner, agent_session_id)

    assert ran.end is RunEnd.ENDED and ran.outcome is LoopOutcome.ERRORED
    assert loop.anthropic.calls == [], "nothing is spent on a loop with no fill"


PLACEMENT = "the egress the agent's allowlist asks for is one its placement never holds"


async def refused_by_its_layer(
    tmp_path: Path, *, clears: bool
) -> tuple[LoopRun, list[AgentStep], AgentWorkspaceTwinImpl, Layer]:
    """A run of a session with a workspace, whose layer refuses that
    workspace before the run's first model call, with a cause."""
    infra = InfraLocalImpl(tmp_path)
    twin = infra.get_agent_workspaces()
    assert isinstance(twin, AgentWorkspaceTwinImpl)
    refusal = IsolationRefused("placement p-3 closes egress", clears=clears, cause=PLACEMENT)
    layer = Layer(refusal=refusal)
    loop = loop_over(tmp_path, kinds=(ASSISTANT, DELIVERY, KEEPER), infra=infra, hooks=layer)
    agent_session_id = await loop.start(KEEPER.name)
    await loop.say(agent_session_id, "Look it up.")
    loop.anthropic.add(reply(use("lookup")), reply(said("Found.")))

    ran = await loop.loops.run(loop.owner, agent_session_id)

    assert loop.anthropic.calls == [], "no model call is spent on a session that cannot run"
    history = await loop.history(agent_session_id)
    assert AgentStepType.MODEL_REQUEST not in [step.type for step in history]
    return ran, history, twin, layer


async def test_a_workspace_the_hooks_refuse_ends_the_loop_before_any_call_naming_its_cause(
    tmp_path: Path,
) -> None:
    """A layer that knows the session's workspace can never be had refuses
    it before the run's first model call, and the loop ends as on a refused
    spec: `errored`, with nothing spent or made, its end naming the cause
    for a person, never the refusal's message."""
    ran, history, twin, layer = await refused_by_its_layer(tmp_path, clears=False)

    assert (ran.end, ran.outcome, ran.park) == (RunEnd.ENDED, LoopOutcome.ERRORED, None)
    ended = history[-1]
    assert isinstance(ended.header, LoopEndedHeader) and ended.header.cause == PLACEMENT
    assert twin.live == set() and layer.prepared == [], "nothing was prepared"


async def test_a_workspace_the_hooks_refuse_until_it_clears_parks_the_loop_on_it(
    tmp_path: Path,
) -> None:
    """A refusal that clears parks the loop on the resource, as a refused
    spec's would, its park naming the cause, and asks again after a wait."""
    ran, _, twin, layer = await refused_by_its_layer(tmp_path, clears=True)

    assert ran.end is RunEnd.PARKED and ran.park is not None, "never ended"
    assert (ran.park.reason, ran.park.unlock, ran.park.cause) == (
        ParkReason.LEASABLE_RESOURCE,
        "agent_workspace",
        PLACEMENT,
    )
    assert ran.park.retry_at is not None, "it asks again after a wait"
    assert twin.live == set() and layer.prepared == [], "nothing was prepared"


def test_attached_toolsets_fold_into_the_kind_and_give_each_tool_once() -> None:
    attached = AttachedToolsets(
        held=(LEDGER,), deferred=(ARCHIVE,), tools=(Lookup("ledger_read"), Lookup("archive_search"))
    )
    folded = with_attached_toolsets(ASSISTANT, attached)
    assert {"ledger_read", "archive_search", LOAD_TOOLSET} <= set(folded.tools)
    assert folded.waiting == frozenset({"archive_search"})
    assert with_attached_toolsets(folded, NO_ATTACHED_TOOLSETS) is folded
    assert attached.names == ("ledger_read", "archive_search", LOAD_TOOLSET)
    with pytest.raises(ValueError, match="given once"):
        AttachedToolsets(held=(LEDGER,), deferred=(), tools=())


@pytest.mark.parametrize("refused", [True, False])
async def test_a_message_needs_every_permission_an_attached_tool_needs(
    tmp_path: Path, refused: bool
) -> None:
    """Who may instruct a session classes the tools its attached toolsets
    give, a deferred one's included, as it classes its kind's: a member may
    not make a call that changes the tenant's configuration, so its message
    is refused, and nothing is appended."""
    write = Lookup("settings_write", authorization_class=ToolClass.CONFIGURATION)
    attached = AttachedToolsets(held=(), deferred=(SETTINGS,), tools=(write,))
    member = context(Role.MEMBER, make_org())
    loop = loop_over(
        tmp_path, owner=member, hooks=Layer(attached if refused else NO_ATTACHED_TOOLSETS)
    )
    agent_session_id = await loop.start()

    if refused:
        with pytest.raises(NotAuthorized, match="manage_members"):
            await loop.say(agent_session_id, "Change the settings.")
    else:
        await loop.say(agent_session_id, "Change the settings.")

    assert len(await loop.history(agent_session_id)) == (0 if refused else 1)


async def test_a_child_private_through_an_attached_secret_tool_marks_its_parent(
    tmp_path: Path,
) -> None:
    """A child whose kind holds no private data, but whose attached tool is
    given a secret, holds credentials: its report carries them to its
    parent, and the parent's outward call, marked by that report, waits for
    a person."""
    public = LEAD.model_copy(
        update={"name": "public_lead", "private_data": False, "tools": (*LEAD.tools, "send")}
    )
    worker = WORKER.model_copy(update={"private_data": False})
    lifted = ToolPolicyLayer(
        rules=tuple(r for r in DEFAULT_CEILINGS.rules if r.authorization_class is not None)
    )
    layer = ChildLayer(AttachedToolsets(held=(VAULT,), deferred=(), tools=(VaultSync(),)))
    loop = loop_over(tmp_path, kinds=(public, worker), ceilings=lifted, hooks=layer)
    parent_id = await loop.start(public.name)
    await loop.say(parent_id, "Find who owes what, and send it.")
    loop.anthropic.add(reply(said("A sub-agent will find it.")))
    await loop.loops.run(loop.owner, parent_id)
    spawn = Spawn(id=new_id(), kind=worker.name, title="who owes", objective="Find who owes.")
    child_id = (await loop.managers.agents.spawn(loop.owner, parent_id, spawn)).id
    (objective,) = await loop.history(child_id)
    submit = call("submit", claim="succeeded", evidence=[str(objective.id)])
    loop.anthropic.add(reply(said("Customer 42 owes 9,000."), submit))
    await loop.loops.run(loop.owner, child_id)
    parent = await loop.managers.agent_sessions.get_agent_session(loop.owner, parent_id)
    assert parent.holds_private, "the report carries what the child holds"
    loop.anthropic.add(reply(use("send", use_id="use_send")), reply(said("Sent.")))

    run = await loop.loops.run(loop.owner, parent_id)

    assert run.end is RunEnd.PARKED and run.park is not None
    assert (run.park.reason, run.park.unlock) == (ParkReason.PERSON, "approval")
    assert loop.tools["send"].ran_as == [], "it holds its child's credentials"


async def test_a_notice_a_layer_appends_before_the_first_call_leaves_the_snapshot_whole(
    tmp_path: Path,
) -> None:
    """A layer's notice comes before its run prepares the workspace, and
    touches nothing of it: the run that wrote it starts from the latest
    snapshot, never from the instance as it stands."""
    infra = InfraLocalImpl(tmp_path)
    twin = infra.get_agent_workspaces()
    assert isinstance(twin, AgentWorkspaceTwinImpl)
    loop = loop_over(
        tmp_path, kinds=(ASSISTANT, DELIVERY, KEEPER), infra=infra, hooks=Layer(changed=CHANGED)
    )
    agent_session_id = await loop.start(KEEPER.name)
    twin.files[agent_session_id] = b"as the first run left it"
    for text in ("Look it up.", "Look again."):
        await loop.say(agent_session_id, text)
        loop.anthropic.add(reply(use("lookup")), reply(said("Found.")))
        assert (await loop.loops.run(loop.owner, agent_session_id)).outcome is LoopOutcome.SUCCEEDED
        # On the twin the files outlive a release; a container's layer does not.
        twin.files[agent_session_id] = b"what a release loses"

    history = await loop.history(agent_session_id)
    assert [s.as_text() for s in history if s.type is AgentStepType.ENVIRONMENT_CHANGED] == [
        CHANGED,
        CHANGED,
    ]
    snapshotted = [s for s in history if s.type is AgentStepType.SNAPSHOTTED]
    assert len(snapshotted) == 2, "one at each run's end"
    assert isinstance(snapshotted[-1].header, SnapshotHeader)
    assert snapshotted[-1].header.snapshot.size == len(b"as the first run left it"), (
        "the second run started from the first run's snapshot"
    )


async def test_a_layer_is_told_of_a_prepared_workspace_before_the_call_that_reached_it_runs(
    tmp_path: Path,
) -> None:
    """A layer's hook on the prepared workspace is asked once its first
    call has prepared it, and its steps are written before that call runs,
    so the model reads them with its answer. A run that reaches no
    workspace prepares none, and the hook is never asked."""
    layer = Layer(rebuilt=REBUILT)
    hands = KEEPER.model_copy(update={"name": "hands", "tools": ("slow", "ledger")})
    loop = loop_over(
        tmp_path, kinds=(ASSISTANT, hands), hooks=layer, extra=(Lookup("ledger", workspace=False),)
    )
    agent_session_id = await loop.start(hands.name)
    await loop.say(agent_session_id, "What does the ledger say?")
    loop.anthropic.add(reply(call("ledger", q="the total")), reply(said("It is 12.")))
    assert (await loop.loops.run(loop.owner, agent_session_id)).outcome is LoopOutcome.SUCCEEDED
    assert layer.prepared == [], "a run that reaches no workspace is never asked"

    await loop.say(agent_session_id, "Find it in the workspace.")
    loop.anthropic.add(reply(use("slow")), reply(said("Found.")))
    slow = loop.tools["slow"]
    running = asyncio.ensure_future(loop.loops.run(loop.owner, agent_session_id))
    await slow.started.wait()
    held = await loop.history(agent_session_id)
    assert [s.as_text() for s in held if s.type is AgentStepType.ENVIRONMENT_CHANGED] == [
        REBUILT
    ], "written before the call that reached the workspace ran"
    slow.release.set()
    done = await running

    assert done.outcome is LoopOutcome.SUCCEEDED
    ((start, workspace),) = layer.prepared
    assert (start.loop_id, start.epoch) == (done.loop_id, done.epoch), "told of its own run"
    assert workspace.id == agent_session_id and workspace.spec == hands.isolation
    assert REBUILT in text_of(loop.anthropic.calls[-1]), "the model read it with the answer"


async def test_a_workspace_that_holds_an_attached_tools_secret_keeps_no_snapshot(
    tmp_path: Path,
) -> None:
    """A snapshot is scanned for the secrets the session's attached tools
    may have injected, beside the catalog's: one that holds such a value is
    refused, nothing names it, and the run keeps its instance."""
    infra = InfraLocalImpl(tmp_path)
    twin = infra.get_agent_workspaces()
    assert isinstance(twin, AgentWorkspaceTwinImpl)
    layer = Layer(AttachedToolsets(held=(VAULT,), deferred=(), tools=(VaultSync(twin),)))
    loop = loop_over(tmp_path, kinds=(ASSISTANT, DELIVERY, KEEPER), infra=infra, hooks=layer)
    await infra.get_secrets().put(loop.owner.org_id, TOKEN, VALUE)
    agent_session_id = await loop.start(KEEPER.name)
    await loop.say(agent_session_id, "Sync the vault.")
    loop.anthropic.add(reply(use("vault_sync")), reply(said("Synced.")))

    ran = await loop.loops.run(loop.owner, agent_session_id)

    assert ran.outcome is LoopOutcome.SUCCEEDED
    assert twin.files[agent_session_id] == f"token={VALUE}".encode()
    types = [step.type for step in await loop.history(agent_session_id)]
    assert AgentStepType.SNAPSHOTTED not in types, "refused for the attached tool's secret"
    assert agent_session_id in twin.live, "the run keeps its instance"
