"""A workspace's snapshot, over the memory storage, the local buckets, and the
twin provider (ADR 1027). A snapshot is kept sealed under its session's key,
under a hash keyed by it, and a step names it. A restore trusts its bytes
only by that hash, and one that does not match loses the workspace before
it starts. A snapshot holds no credential and no injected secret's value.
The loop snapshots a workspace kept by snapshots at the end of each run, and
the next run starts from it. A rewind starts the next loop from an earlier
snapshot, a step records it, and every earlier step and snapshot stays; one
that cannot load parks once, and a person's unlock goes on without it. A
child forks its parent's latest snapshot into a copy of its own, and a fork
with none spends nothing of the tree."""

from pathlib import Path
from uuid import UUID

import pytest
from contracts.doubles import context
from contracts.factories import make_org
from contracts.loops import (
    ASSISTANT,
    DELIVERY,
    Asked,
    Found,
    Lookup,
    Loop,
    call,
    loop_over,
    reply,
    said,
)
from contracts.tools import Tools, tools_over, twin_transport

from acme.infra.buckets import Buckets
from acme.infra.buckets.local import BucketsLocalImpl
from acme.infra.impl.local import InfraLocalImpl
from acme.infra.secrets.local import SecretsLocalImpl
from acme.infra.transports import SecretUse, SecretVia
from acme.infra.transports.broker import BrokerTwinImpl
from acme.infra.workspaces import (
    Durability,
    EgressMode,
    EgressPolicy,
    IsolationMode,
    IsolationRefused,
    IsolationSpec,
    SnapshotRefused,
    Workspace,
    WorkspaceLost,
)
from acme.infra.workspaces.host import UNKEPT, WorkspaceHostImpl
from acme.infra.workspaces.twin import WorkspaceTwinImpl
from acme.om.agent_sessions.rules import QUESTION
from acme.om.agents.loop_rules import FORKED, LOST, REWOUND, pending_restore
from acme.om.agents.types.request import Spawn
from acme.om.agents.types.run import RunEnd
from acme.om.base import Platform, new_id, utcnow
from acme.om.context import Role, TenantContext
from acme.om.exceptions import NotFound, ValidationFailed
from acme.om.steps.rules import control_step
from acme.om.steps.types.header import (
    ControlCommand,
    ControlHeader,
    LoopOutcome,
    ParkReason,
    SnapshotHeader,
    WorkspaceSnapshot,
)
from acme.om.steps.types.step import Step, StepType
from acme.om.tools.impl.snapshots import BUCKET, session_prefix, snapshot_key
from acme.om.tools.native.ask_person import ASK_PERSON
from acme.om.tools.native.spawn_sub_agent import SPAWN_SUB_AGENT
from acme.om.tools.native.wait_for_sub_agents import WAIT_FOR_SUB_AGENTS
from acme.om.tools.tool import ToolRuntime
from acme.om.tools.types.tool import Effect, ToolClass, ToolInput

KEPT = IsolationSpec(
    mode=IsolationMode.TWIN,
    egress=EgressPolicy(mode=EgressMode.NONE),
    durability=Durability.SNAPSHOT,
)


class Kept:
    """The tools over the twin provider, the twin broker, and buckets of
    their own, with one injected secret the catalog may give a command."""

    def __init__(self, tmp_path: Path) -> None:
        transport, _ = twin_transport(tmp_path)
        self.provider = WorkspaceTwinImpl()
        self.broker = BrokerTwinImpl()
        self.buckets = BucketsLocalImpl(tmp_path / "buckets")
        self.secrets = SecretsLocalImpl(tmp_path / "secrets.env")
        self.tools: Tools = tools_over(
            transport,
            self.provider,
            broker=self.broker,
            buckets=self.buckets,
            secrets=self.secrets,
            secret_names=frozenset({"SERVICE_TOKEN", "UNSET_TOKEN"}),
        )
        self.ctx = context(Role.SERVICE, make_org())

    async def workspace(self, session_id: UUID, files: bytes) -> Workspace:
        workspace = await self.tools.manager.prepare_workspace(self.ctx, session_id, KEPT)
        self.provider.files[session_id] = files
        return workspace

    async def snapshot(self, workspace: Workspace) -> Step:
        epoch = await self.tools.steps.begin_run(self.ctx, workspace.id)
        return await self.tools.manager.snapshot_workspace(
            self.ctx, workspace.id, workspace, epoch=epoch, loop_id=new_id()
        )

    async def stored(self, session_id: UUID) -> list[str]:
        return await self.buckets.list(
            self.ctx.org_id, Buckets.SNAPSHOTS, session_prefix(session_id), 100
        )


def named(step: Step) -> WorkspaceSnapshot:
    assert isinstance(step.header, SnapshotHeader)
    return step.header.snapshot


async def test_a_snapshot_is_sealed_kept_under_its_keyed_hash_and_named_by_a_step(
    tmp_path: Path,
) -> None:
    kept = Kept(tmp_path)
    session_id = new_id()
    workspace = await kept.workspace(session_id, b"the files the commands wrote")

    step = await kept.snapshot(workspace)

    snapshot = named(step)
    assert step.type is StepType.SNAPSHOTTED and step.seq > 0, "the history names it"
    assert snapshot.hash.startswith("hmac-sha256:") and snapshot.size == 28
    assert snapshot.workspace_id == session_id
    (key,) = await kept.stored(session_id)
    assert key == snapshot_key(session_id, snapshot.hash)
    sealed = await kept.buckets.get(kept.ctx.org_id, BUCKET, key)
    assert b"the files the commands wrote" not in sealed, "sealed, never in the clear"
    assert session_id in kept.provider.live, "the workspace keeps its instance"


async def test_a_restore_starts_the_workspace_from_the_snapshot_its_step_names(
    tmp_path: Path,
) -> None:
    kept = Kept(tmp_path)
    session_id = new_id()
    workspace = await kept.workspace(session_id, b"kept")
    snapshot = named(await kept.snapshot(workspace))
    kept.provider.files[session_id] = b"changed since"
    await kept.tools.manager.release_workspace(kept.ctx, workspace)

    restored = await kept.tools.manager.prepare_workspace(
        kept.ctx, session_id, KEPT, restore=snapshot
    )

    assert restored.id == session_id and session_id in kept.provider.live
    assert kept.provider.files[session_id] == b"kept"


@pytest.mark.parametrize("damage", ["altered", "gone", "another hash"])
async def test_a_snapshot_that_does_not_match_its_step_loses_the_workspace_before_it_starts(
    tmp_path: Path, damage: str
) -> None:
    """Bytes altered in the store, bytes gone, or a step that names another
    hash: the workspace is lost, loudly, and the provider never starts one,
    so nothing runs on a state nobody kept."""
    kept = Kept(tmp_path)
    session_id = new_id()
    workspace = await kept.workspace(session_id, b"kept")
    snapshot = named(await kept.snapshot(workspace))
    await kept.provider.release(workspace)
    key = snapshot_key(session_id, snapshot.hash)
    if damage == "altered":
        sealed = bytearray(await kept.buckets.get(kept.ctx.org_id, BUCKET, key))
        sealed[-1] ^= 0x01
        await kept.buckets.put(
            kept.ctx.org_id, BUCKET, key, bytes(sealed), "application/octet-stream"
        )
    elif damage == "gone":
        await kept.buckets.delete(kept.ctx.org_id, BUCKET, key)
    else:
        sealed = await kept.buckets.get(kept.ctx.org_id, BUCKET, key)
        snapshot = snapshot.model_copy(update={"hash": "hmac-sha256:" + "0" * 64})
        await kept.buckets.put(
            kept.ctx.org_id,
            BUCKET,
            snapshot_key(session_id, snapshot.hash),
            sealed,
            "application/octet-stream",
        )

    with pytest.raises(WorkspaceLost):
        await kept.tools.manager.prepare_workspace(kept.ctx, session_id, KEPT, restore=snapshot)

    assert session_id not in kept.provider.live, "no workspace started"


async def test_a_snapshot_takes_back_every_credential_and_refuses_a_secrets_value(
    tmp_path: Path,
) -> None:
    """A credential the broker still holds there, one a lost run never took
    back included, is taken back before the snapshot is taken. A workspace
    that holds an injected secret's value, raw or encoded, keeps no
    snapshot: the refusal names the secret, and nothing is stored."""
    kept = Kept(tmp_path)
    await kept.secrets.put(kept.ctx.org_id, "SERVICE_TOKEN", "tok-0123456789abcdef")
    session_id = new_id()
    workspace = await kept.workspace(session_id, b"clean files")
    brokered = SecretUse(name="deploy-key", via=SecretVia.BROKERED, destination="git.example")
    await kept.broker.attach(workspace, new_id(), brokered)

    await kept.snapshot(workspace)
    assert kept.broker.attached == {}, "taken back before the snapshot"

    for leaked in (b"token=tok-0123456789abcdef\n", b"dG9rLTAxMjM0NTY3ODlhYmNkZWY="):
        kept.provider.files[session_id] = leaked
        before = await kept.stored(session_id)
        with pytest.raises(SnapshotRefused, match="SERVICE_TOKEN") as refused:
            await kept.snapshot(workspace)
        assert "tok-0123456789abcdef" not in refused.value.message
        assert await kept.stored(session_id) == before, "nothing kept"


async def test_a_host_workspace_refuses_a_snapshot_with_its_reason_before_anything_runs(
    tmp_path: Path,
) -> None:
    """A session whose spec asks for snapshots is refused at prepare, before
    any command can run; a host workspace asked for a snapshot refuses it,
    and nothing is stored or named."""
    transport, _ = twin_transport(tmp_path)
    root = tmp_path / "workspaces"
    tools = tools_over(
        transport, WorkspaceHostImpl(root), buckets=BucketsLocalImpl(tmp_path / "buckets")
    )
    ctx = context(Role.SERVICE, make_org())
    asked = IsolationSpec(
        mode=IsolationMode.HOST,
        egress=EgressPolicy(mode=EgressMode.OPEN),
        durability=Durability.SNAPSHOT,
    )
    with pytest.raises(IsolationRefused, match=UNKEPT):
        await tools.manager.prepare_workspace(ctx, new_id(), asked)
    assert not root.exists(), "nothing made"
    session_id = new_id()
    cached = asked.model_copy(update={"durability": Durability.CACHE})
    workspace = await tools.manager.prepare_workspace(ctx, session_id, cached)
    epoch = await tools.steps.begin_run(ctx, session_id)
    with pytest.raises(SnapshotRefused, match=UNKEPT):
        await tools.manager.snapshot_workspace(
            ctx, session_id, workspace, epoch=epoch, loop_id=new_id()
        )
    assert (await tools.steps.get_cursor(ctx, session_id)).head == 0, "no step names one"


async def test_a_purge_removes_every_snapshot_of_its_session_and_none_of_anothers(
    tmp_path: Path,
) -> None:
    kept = Kept(tmp_path)
    first, second = new_id(), new_id()
    for files in (b"one", b"two"):
        await kept.snapshot(await kept.workspace(first, files))
    await kept.snapshot(await kept.workspace(second, b"other"))
    assert len(await kept.stored(first)) == 2

    await kept.tools.manager.purge_workspace(kept.ctx.org_id, first)

    assert await kept.stored(first) == []
    assert len(await kept.stored(second)) == 1


async def test_a_session_finds_only_the_snapshots_its_own_history_names(tmp_path: Path) -> None:
    kept = Kept(tmp_path)
    mine, theirs = new_id(), new_id()
    own = named(await kept.snapshot(await kept.workspace(mine, b"mine")))
    other = named(await kept.snapshot(await kept.workspace(theirs, b"theirs")))
    assert await kept.tools.manager.find_snapshot(kept.ctx, mine, own.id) == own
    with pytest.raises(NotFound):
        await kept.tools.manager.find_snapshot(kept.ctx, mine, other.id)


# Over the loop.


class Scribble(Lookup):
    """A command's writes, on the twin: its input becomes all the workspace's
    files hold, as a command that writes inside and outside a container's
    volume would leave them."""

    def __init__(self, twin: WorkspaceTwinImpl) -> None:
        super().__init__("scribble", effect=Effect.IDEMPOTENT, authorization_class=ToolClass.WRITE)
        self._twin = twin

    async def run(
        self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime
    ) -> Platform:
        assert isinstance(call_input, Asked)
        self._twin.files[runtime.session_id] = call_input.q.encode()
        return Found(text="written")


KEEPER = ASSISTANT.model_copy(
    update={
        "name": "keeper",
        "isolation": KEPT,
        "tools": (*ASSISTANT.tools, "scribble", ASK_PERSON),
    }
)
FORKER = ASSISTANT.model_copy(
    update={
        "name": "forker",
        "isolation": KEPT,
        "tools": (*ASSISTANT.tools, "scribble", SPAWN_SUB_AGENT, WAIT_FOR_SUB_AGENTS),
    }
)


def kept_loop(tmp_path: Path) -> tuple[Loop, WorkspaceTwinImpl]:
    """A loop over the twin, with kinds that keep their workspace by
    snapshots and a tool that writes into it."""
    infra = InfraLocalImpl(tmp_path)
    twin = infra.get_workspaces()
    assert isinstance(twin, WorkspaceTwinImpl)
    loop = loop_over(
        tmp_path, kinds=(ASSISTANT, DELIVERY, KEEPER, FORKER), infra=infra, extra=(Scribble(twin),)
    )
    return loop, twin


async def wrote(loop: Loop, session_id: UUID, files: bytes) -> WorkspaceSnapshot:
    """A loop that writes `files` into the workspace and ends: its run
    snapshots the workspace at its end, and the step names the snapshot."""
    await loop.say(session_id, "Write it.")
    loop.anthropic.add(reply(call("scribble", q=files.decode())), reply(said("Written.")))
    assert (await loop.loops.run(loop.owner, session_id)).outcome is LoopOutcome.SUCCEEDED
    return named(snapshots(await loop.history(session_id))[-1])


def snapshots(history: list[Step]) -> list[Step]:
    return [step for step in history if step.type is StepType.SNAPSHOTTED]


async def rewind(loop: Loop, session_id: UUID, snapshot_id: UUID) -> Step:
    ctx: TenantContext = loop.owner
    snapshot = await loop.managers.tools.find_snapshot(ctx, session_id, snapshot_id)
    control = control_step(
        new_id(), loop.clock.now, session_id, ctx, ControlCommand.RESTORE, snapshot=snapshot
    )
    (stored,), _ = await loop.managers.agent_sessions.receive(ctx, session_id, [control])
    return stored


async def test_a_kept_workspace_is_snapshotted_at_each_runs_end_and_the_next_run_starts_from_it(
    tmp_path: Path,
) -> None:
    """The run that parks snapshots the workspace before its park, then lets
    the instance go. The resumed run, and a later loop, start from that
    snapshot: what a release lost is back."""
    loop, twin = kept_loop(tmp_path)
    session_id = await loop.start(KEEPER.name)
    await loop.say(session_id, "Install the tool, then ask me which version.")
    loop.anthropic.add(
        reply(call("scribble", q="the tool, installed")),
        reply(call(ASK_PERSON, question="Which version?")),
    )

    parked = await loop.loops.run(loop.owner, session_id)

    assert parked.end is RunEnd.PARKED and parked.park == QUESTION
    history = await loop.history(session_id)
    assert [step.type for step in history[-2:]] == [StepType.SNAPSHOTTED, StepType.PARKED]
    assert named(history[-2]).workspace_id == session_id
    assert session_id not in twin.live, "its instance went once the snapshot held it"
    for text in ("Version 1.", "Check it again."):
        # On the twin the files outlive a release; a container's layer does not.
        twin.files[session_id] = b"what a release loses"
        await loop.say(session_id, text)
        loop.anthropic.add(reply(said("It is there.")))
        assert (await loop.loops.run(loop.owner, session_id)).outcome is LoopOutcome.SUCCEEDED
        assert twin.files[session_id] == b"the tool, installed"
    assert len(snapshots(await loop.history(session_id))) == 3, "one at each run's end"


async def test_a_run_whose_snapshot_fails_keeps_its_instance_and_the_next_starts_as_it_stands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A snapshot refused at a run's end, such as for a secret's value in the
    workspace, keeps nothing and names nothing: the instance stays live, and
    the next run goes on from it, never from the older snapshot."""
    loop, twin = kept_loop(tmp_path)
    session_id = await loop.start(KEEPER.name)
    await wrote(loop, session_id, b"kept at the first end")

    async def refused(*args: object, **kwargs: object) -> Step:
        raise SnapshotRefused("the workspace holds the secret 'SERVICE_TOKEN'")

    with monkeypatch.context() as patched:
        patched.setattr(loop.managers.tools, "snapshot_workspace", refused)
        await loop.say(session_id, "Change it.")
        loop.anthropic.add(reply(call("scribble", q="written since")), reply(said("Changed.")))
        assert (await loop.loops.run(loop.owner, session_id)).outcome is LoopOutcome.SUCCEEDED
    assert session_id in twin.live, "no snapshot holds it, so its instance stays"
    assert len(snapshots(await loop.history(session_id))) == 1

    await loop.say(session_id, "Look again.")
    loop.anthropic.add(reply(said("Seen.")))
    assert (await loop.loops.run(loop.owner, session_id)).outcome is LoopOutcome.SUCCEEDED
    assert twin.files[session_id] == b"written since"


async def test_a_workspace_a_person_worked_in_by_hand_is_never_replaced_by_an_older_snapshot(
    tmp_path: Path,
) -> None:
    loop, twin = kept_loop(tmp_path)
    session_id = await loop.start(KEEPER.name)
    await wrote(loop, session_id, b"as the agent left it")
    await loop.loops.take_over(loop.owner, session_id)
    twin.files[session_id] = b"as the person fixed it"
    await loop.loops.give_back(loop.owner, session_id, "Fixed the config by hand.")
    loop.anthropic.add(reply(said("Seen.")))

    assert (await loop.loops.run(loop.owner, session_id)).outcome is LoopOutcome.SUCCEEDED

    assert twin.files[session_id] == b"as the person fixed it"
    latest = named(snapshots(await loop.history(session_id))[-1])
    assert latest.size == len(b"as the person fixed it"), "the run kept what the person left"


async def test_a_rewind_starts_the_next_loop_from_the_earlier_snapshot_and_keeps_all_before_it(
    tmp_path: Path,
) -> None:
    loop, twin = kept_loop(tmp_path)
    session_id = await loop.start(KEEPER.name)
    earlier = await wrote(loop, session_id, b"the records as first set up")
    later = await wrote(loop, session_id, b"the records after a bad change")
    before = await loop.history(session_id)

    control = await rewind(loop, session_id, earlier.id)
    await loop.say(session_id, "Go on from the earlier state.")
    loop.anthropic.add(reply(said("Going on.")))
    run = await loop.loops.run(loop.owner, session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED
    assert twin.files[session_id] == b"the records as first set up"
    history = await loop.history(session_id)
    assert history[: len(before)] == before, "every earlier step stays as it was"
    (done,) = [step for step in history if control.id in step.refs]
    assert done.type is StepType.ENVIRONMENT_CHANGED and done.as_text() == REWOUND
    assert pending_restore(history) is None, "done once"
    request = loop.anthropic.calls[-1].model_dump_json()
    assert REWOUND in request, "the model reads that its workspace changed"
    stored = await loop.infra.get_buckets().list(
        loop.owner.org_id, Buckets.SNAPSHOTS, session_prefix(session_id), 10
    )
    assert sorted(stored) == sorted(snapshot_key(session_id, s.hash) for s in (earlier, later)), (
        "every snapshot stays"
    )

    # The next loop starts from what the rewound run kept, and the rewind is
    # never done again.
    await wrote(loop, session_id, b"written since the rewind")
    await loop.say(session_id, "One more.")
    loop.anthropic.add(reply(said("Done.")))
    await loop.loops.run(loop.owner, session_id)
    assert twin.files[session_id] == b"written since the rewind"
    notices = [step for step in await loop.history(session_id) if control.id in step.refs]
    assert notices == [done]


@pytest.mark.parametrize("asked", ["a rewind", "the next run"])
async def test_a_restore_whose_bytes_are_gone_parks_once_and_the_unlock_goes_on_without_it(
    tmp_path: Path, asked: str
) -> None:
    """A rewind, or the next run's start from the latest snapshot, whose bytes
    are gone: the loop parks for a person, with no model call. The person's
    unlock goes on from the workspace as it stands, a model call follows, and
    a notice that references what named the snapshot records the restore
    lost, which the model reads. It is never tried again."""
    loop, twin = kept_loop(tmp_path)
    session_id = await loop.start(KEEPER.name)
    earlier = await wrote(loop, session_id, b"the earlier state")
    latest = await wrote(loop, session_id, b"the latest state")
    gone = earlier if asked == "a rewind" else latest
    await loop.infra.get_buckets().delete(
        loop.owner.org_id, BUCKET, snapshot_key(session_id, gone.hash)
    )
    if asked == "a rewind":
        source = await rewind(loop, session_id, earlier.id)
    else:
        (source,) = [s for s in snapshots(await loop.history(session_id)) if named(s) == latest]
    await loop.say(session_id, "Go on.")
    calls = len(loop.anthropic.calls)

    parked = await loop.loops.run(loop.owner, session_id)

    assert parked.park is not None
    assert (parked.park.reason, parked.park.unlock) == (ParkReason.PERSON, "workspace")
    assert len(loop.anthropic.calls) == calls, "no model call on a lost restore"

    unlock = control_step(new_id(), utcnow(), session_id, loop.owner, ControlCommand.UNLOCK)
    await loop.managers.agent_sessions.receive(loop.owner, session_id, [unlock])
    loop.anthropic.add(reply(said("Going on as it stands.")))
    run = await loop.loops.run(loop.owner, session_id)

    assert run.outcome is LoopOutcome.SUCCEEDED
    assert len(loop.anthropic.calls) == calls + 1, "a model call follows the unlock"
    assert twin.files[session_id] == b"the latest state", "the workspace as it stood"
    history = await loop.history(session_id)
    (lost,) = [step for step in history if source.id in step.refs]
    assert lost.type is StepType.ENVIRONMENT_CHANGED and lost.as_text() == LOST
    assert LOST in loop.anthropic.calls[-1].model_dump_json(), "the model reads it was lost"
    assert pending_restore(history) is None

    await loop.say(session_id, "And again.")
    loop.anthropic.add(reply(said("Fine.")))
    again = await loop.loops.run(loop.owner, session_id)
    assert again.outcome is LoopOutcome.SUCCEEDED, "parked once, never again"


async def test_a_child_forks_its_parents_latest_snapshot_into_a_copy_of_its_own(
    tmp_path: Path,
) -> None:
    loop, twin = kept_loop(tmp_path)
    parent = await loop.start(FORKER.name)
    await wrote(loop, parent, b"an older state")
    latest = await wrote(loop, parent, b"the parent's files")

    child = await loop.managers.agents.spawn(
        loop.owner,
        parent,
        Spawn(
            id=new_id(),
            kind=FORKER.name,
            title="a try",
            objective="Try the change on a copy. Report what holds.",
            fork=True,
        ),
    )
    (restore,) = [s for s in await loop.history(child.id) if s.type is StepType.CONTROL]
    assert isinstance(restore.header, ControlHeader)
    copy = restore.header.snapshot
    assert copy is not None and copy.workspace_id == parent and copy.id != latest.id
    loop.anthropic.add(reply(said("It holds.")))
    await loop.loops.run(loop.owner, child.id)

    assert twin.files[child.id] == b"the parent's files"
    (notice,) = [step for step in await loop.history(child.id) if restore.id in step.refs]
    assert notice.as_text() == FORKED
    twin.files[child.id] = b"the child's own writes"
    assert twin.files[parent] == b"the parent's files"
    # The copy is the child's own: the parent's purge leaves it.
    await loop.managers.tools.purge_workspace(loop.owner.org_id, parent)
    kept = await loop.infra.get_buckets().list(
        loop.owner.org_id, Buckets.SNAPSHOTS, session_prefix(child.id), 10
    )
    assert kept == [snapshot_key(child.id, copy.hash)]


async def test_a_fork_with_no_snapshot_to_start_from_spends_nothing_of_the_tree(
    tmp_path: Path,
) -> None:
    """Refused before a slot is taken or a child is made: the tree's size is
    as it was, and no child session exists, so a retry finds nothing spent."""
    loop, _ = kept_loop(tmp_path)
    parent = await loop.start(FORKER.name)
    before = (await loop.managers.agents.tree_of(loop.owner, parent)).size
    asked = Spawn(id=new_id(), kind=FORKER.name, title="a try", objective="Try it.", fork=True)

    for _ in range(2):
        with pytest.raises(ValidationFailed, match="holds no snapshot"):
            await loop.managers.agents.spawn(loop.owner, parent, asked)

    assert (await loop.managers.agents.tree_of(loop.owner, parent)).size == before
    with pytest.raises(NotFound):
        await loop.managers.agent_sessions.get_session(loop.owner, asked.id)
