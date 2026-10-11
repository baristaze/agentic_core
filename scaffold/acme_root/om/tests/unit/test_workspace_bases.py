"""Workspace bases, over the memory storage, the local buckets, the twin
provider, and the twin transport (ADR 1028). A base is built once for its
tenant and kept as a snapshot every workspace on it starts from; a changed
base is another one. Its setup runs in a workspace of its own, with the
setup's egress, given no secret. A failed setup keeps nothing, and the next
prepare builds again. Two prepares racing on one base build it once."""

import asyncio
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from contracts.doubles import context
from contracts.factories import make_org
from contracts.tools import Tools, tools_over

from acme.infra.buckets import Buckets
from acme.infra.buckets.local import BucketsLocalImpl
from acme.infra.secrets.local import SecretsLocalImpl
from acme.infra.transports import CommandSpec
from acme.infra.transports.broker import BrokerTwinImpl
from acme.infra.transports.twin import TransportTwinImpl, TwinReply
from acme.infra.workspaces import (
    BaseSetupFailed,
    Durability,
    EgressMode,
    EgressPolicy,
    IsolationMode,
    IsolationRefused,
    IsolationSpec,
    Workspace,
    WorkspaceBase,
    WorkspaceLost,
)
from acme.infra.workspaces.twin import WorkspaceTwinImpl
from acme.om.base import new_id
from acme.om.context import Role, TenantContext
from acme.om.steps.types.header import SnapshotHeader
from acme.om.tools.impl.bases import BUCKET, PREFIX, base_key
from acme.om.tools.impl.manager import ToolsOptions

IMAGE = "python:3.14-slim"
OPEN = EgressPolicy(mode=EgressMode.OPEN)
NONE = EgressPolicy(mode=EgressMode.NONE)
SETUP = ("pip install acme-tools", "echo built >> /opt/count")
BASE = WorkspaceBase(image=IMAGE, setup=SETUP, egress=OPEN)
ON_BASE = IsolationSpec(mode=IsolationMode.TWIN, egress=NONE, base=BASE)
TOKEN = "tok-0123456789abcdef"


class Setup:
    """The twin transport's handler: each command it runs, the environment
    it gets, and the exit code a case asks of a command."""

    def __init__(self) -> None:
        self.ran: list[CommandSpec] = []
        self.envs: list[Mapping[str, str]] = []
        self.exits: dict[str, int] = {}
        self.gate: asyncio.Event | None = None
        self.started = asyncio.Event()

    async def __call__(self, command: CommandSpec, env: Mapping[str, str]) -> TwinReply:
        self.ran.append(command)
        self.envs.append(dict(env))
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        return TwinReply(exit_code=self.exits.get(command.argv[-1], 0))

    def commands(self) -> list[str]:
        return [command.argv[-1] for command in self.ran]


class BuiltTwin(WorkspaceTwinImpl):
    """The twin provider: what a snapshot of a workspace holds is what a
    test wrote there and each command its transport ran there, so a base
    holds its setup. Each spec it prepared to is kept, in order."""

    def __init__(self, transport: TransportTwinImpl) -> None:
        super().__init__()
        self._transport = transport
        self.specs: list[tuple[Workspace, IsolationSpec]] = []
        self.lost = False

    async def prepare(
        self,
        org_id: UUID,
        workspace_id: UUID,
        spec: IsolationSpec,
        snapshot: bytes | None = None,
        base: bytes | None = None,
    ) -> Workspace:
        if self.lost and base is not None:
            raise WorkspaceLost("the image the base was built on is gone")
        workspace = await super().prepare(org_id, workspace_id, spec, snapshot, base)
        self.specs.append((workspace, spec))
        return workspace

    async def snapshot(self, workspace: Workspace) -> bytes:
        ran = [
            command.argv[-1]
            for command in self._transport.commands
            if (workspace.id, command.key) in self._transport.records
        ]
        return await super().snapshot(workspace) + "\n".join(ran).encode()


class Bases:
    """The tools over the built twin, with buckets of their own, and one
    secret a command of the catalog may be given."""

    def __init__(self, tmp_path: Path, options: ToolsOptions | None = None) -> None:
        self.setup = Setup()
        self.secrets = SecretsLocalImpl(tmp_path / "secrets.env")
        self.transport = TransportTwinImpl(self.secrets, BrokerTwinImpl(), self.setup)
        self.provider = BuiltTwin(self.transport)
        self.buckets = BucketsLocalImpl(tmp_path / "buckets")
        self.tools: Tools = tools_over(
            self.transport,
            self.provider,
            options,
            buckets=self.buckets,
            secrets=self.secrets,
            secret_names=frozenset({"SERVICE_TOKEN"}),
        )
        self.ctx = context(Role.SERVICE, make_org())

    async def prepare(
        self, spec: IsolationSpec = ON_BASE, ctx: TenantContext | None = None
    ) -> Workspace:
        return await self.tools.manager.prepare_workspace(ctx or self.ctx, new_id(), spec)

    async def kept(self, ctx: TenantContext | None = None) -> list[str]:
        org_id = (ctx or self.ctx).org_id
        return await self.buckets.list(org_id, Buckets.SNAPSHOTS, PREFIX, 100)

    def builds(self) -> list[Workspace]:
        """Each workspace a setup ran in."""
        return [workspace for workspace, spec in self.provider.specs if spec.base != BASE]


async def test_a_base_is_built_once_and_every_workspace_on_it_starts_from_its_snapshot(
    tmp_path: Path,
) -> None:
    bases = Bases(tmp_path)

    first = await bases.prepare()
    second = await bases.prepare()

    assert bases.setup.commands() == list(SETUP), "the setup ran once, in order"
    snapshot = bases.provider.bases[first.id]
    assert bases.provider.bases[second.id] == snapshot, "both start from the same snapshot"
    assert b"echo built >> /opt/count" in snapshot, "what the setup wrote is in it"
    assert await bases.kept() == [base_key(ON_BASE)]
    (build,) = bases.builds()
    assert build.id not in bases.provider.live, "the build's workspace is gone"
    assert not [key for key in bases.transport.records if key[0] == build.id]

    changed = ON_BASE.model_copy(
        update={"base": BASE.model_copy(update={"setup": (SETUP[0], "echo again >> /opt/count")})}
    )
    third = await bases.prepare(changed)

    assert bases.setup.commands()[2:] == [SETUP[0], "echo again >> /opt/count"]
    assert bases.provider.bases[third.id] != snapshot, "a changed base is another one"
    assert sorted(await bases.kept()) == sorted([base_key(ON_BASE), base_key(changed)])


def test_a_base_is_keyed_by_its_mode_its_image_its_commands_and_its_egress() -> None:
    """Each part of the base is in its key, so no change to one is served
    the base before it."""
    keys = {
        base_key(ON_BASE),
        base_key(ON_BASE.model_copy(update={"mode": IsolationMode.CONTAINER})),
        base_key(ON_BASE.model_copy(update={"base": BASE.model_copy(update={"image": "x:1"})})),
        base_key(ON_BASE.model_copy(update={"base": BASE.model_copy(update={"setup": SETUP[:1]})})),
        base_key(ON_BASE.model_copy(update={"base": BASE.model_copy(update={"egress": NONE})})),
    }
    assert len(keys) == 5
    assert base_key(ON_BASE.model_copy(update={"egress": OPEN})) == base_key(ON_BASE), (
        "the workspace's own egress is no part of its base"
    )


async def test_the_setup_runs_with_its_own_egress_and_the_workspace_with_its_spec(
    tmp_path: Path,
) -> None:
    bases = Bases(tmp_path)

    workspace = await bases.prepare()

    (build,) = bases.builds()
    setup_spec = next(spec for found, spec in bases.provider.specs if found.id == build.id)
    assert setup_spec.egress == OPEN, "the setup reaches its registry"
    assert setup_spec.durability is Durability.SNAPSHOT
    assert setup_spec.base == WorkspaceBase(image=IMAGE), "on the base's image alone"
    assert workspace.spec.egress == NONE, "the workspace never holds the setup's egress"


async def test_the_setup_is_given_no_secret_and_no_session_content(tmp_path: Path) -> None:
    bases = Bases(tmp_path)
    await bases.secrets.put(bases.ctx.org_id, "SERVICE_TOKEN", TOKEN)

    await bases.prepare()

    assert [command.secrets for command in bases.setup.ran] == [(), ()]
    assert [command.env for command in bases.setup.ran] == [(), ()]
    assert bases.setup.envs == [{}, {}], "nothing but the image's own environment"
    assert [command.argv[:2] for command in bases.setup.ran] == [("sh", "-c")] * 2


async def test_a_failed_setup_keeps_nothing_and_the_next_prepare_builds_again(
    tmp_path: Path,
) -> None:
    bases = Bases(tmp_path)
    bases.setup.exits[SETUP[0]] = 3

    with pytest.raises(BaseSetupFailed) as failed:
        await bases.prepare()

    assert failed.value.command == SETUP[0] and failed.value.exit_code == 3
    assert "command 1, 'pip install acme-tools', exited 3" in failed.value.message
    assert not failed.value.clears, "a setup that fails ends the loop, and says why"
    assert bases.setup.commands() == [SETUP[0]], "nothing runs past a failed command"
    assert await bases.kept() == [], "no half-built base"
    assert bases.provider.live == set(), "nor its workspace"

    bases.setup.exits.clear()
    workspace = await bases.prepare()

    assert bases.setup.commands() == [SETUP[0], *SETUP], "built again, whole"
    assert workspace.id in bases.provider.bases


async def test_a_setup_that_runs_out_of_time_keeps_nothing(tmp_path: Path) -> None:
    bases = Bases(tmp_path, ToolsOptions(base_build_limit=timedelta(0)))

    with pytest.raises(BaseSetupFailed, match="ran out of time") as failed:
        await bases.prepare()

    assert failed.value.exit_code is None
    assert await bases.kept() == []


async def test_two_prepares_racing_on_one_base_build_it_once(tmp_path: Path) -> None:
    """One holds the claim and builds; the other is refused with a refusal
    that clears, so its loop waits and asks again, and then starts from the
    one build."""
    bases = Bases(tmp_path)
    bases.setup.gate = asyncio.Event()
    building = asyncio.create_task(bases.prepare())
    await bases.setup.started.wait()

    with pytest.raises(IsolationRefused, match="is being built") as waiting:
        await bases.prepare()
    assert waiting.value.clears

    bases.setup.gate.set()
    first = await building
    second = await bases.prepare()

    assert bases.setup.commands() == list(SETUP), "built once"
    assert bases.provider.bases[first.id] == bases.provider.bases[second.id]


async def test_a_base_is_its_tenants_alone(tmp_path: Path) -> None:
    bases = Bases(tmp_path)
    other = context(Role.SERVICE, make_org())

    mine = await bases.prepare()
    theirs = await bases.prepare(ctx=other)

    assert bases.setup.commands() == [*SETUP, *SETUP], "another tenant builds its own"
    assert await bases.kept() == [base_key(ON_BASE)]
    assert await bases.kept(other) == [base_key(ON_BASE)]
    assert mine.org_id != theirs.org_id


async def test_a_restore_starts_from_its_snapshot_and_reads_no_base(tmp_path: Path) -> None:
    bases = Bases(tmp_path)
    session_id = new_id()
    workspace = await bases.tools.manager.prepare_workspace(bases.ctx, session_id, ON_BASE)
    epoch = await bases.tools.steps.begin_run(bases.ctx, session_id)
    step = await bases.tools.manager.snapshot_workspace(
        bases.ctx, session_id, workspace, epoch=epoch, loop_id=new_id()
    )
    assert isinstance(step.header, SnapshotHeader)
    snapshot = step.header.snapshot
    for key in await bases.kept():
        await bases.buckets.delete(bases.ctx.org_id, BUCKET, key)
    ran = len(bases.setup.ran)

    restored = await bases.tools.manager.prepare_workspace(
        bases.ctx, session_id, ON_BASE, restore=snapshot
    )

    assert len(bases.setup.ran) == ran, "no base is built for a restore"
    assert restored.id == session_id


async def test_a_base_the_provider_cannot_bring_in_is_built_again(tmp_path: Path) -> None:
    """Nothing of the session is in a base, so one the host cannot start is
    dropped, and the loop asks again: the next prepare builds it anew."""
    bases = Bases(tmp_path)
    await bases.prepare()
    bases.provider.lost = True

    with pytest.raises(IsolationRefused, match="built again") as refused:
        await bases.prepare()

    assert refused.value.clears
    assert await bases.kept() == []
    bases.provider.lost = False
    await bases.prepare()
    assert bases.setup.commands() == [*SETUP, *SETUP]


async def test_a_base_that_holds_a_secrets_value_is_not_kept(tmp_path: Path) -> None:
    bases = Bases(tmp_path)
    await bases.secrets.put(bases.ctx.org_id, "SERVICE_TOKEN", TOKEN)
    leaky = ON_BASE.model_copy(
        update={"base": BASE.model_copy(update={"setup": (f"echo {TOKEN} > /opt/token",)})}
    )

    with pytest.raises(IsolationRefused, match="SERVICE_TOKEN") as refused:
        await bases.prepare(leaky)

    assert TOKEN not in refused.value.message
    assert await bases.kept() == []


async def test_a_base_with_no_setup_is_the_image_alone(tmp_path: Path) -> None:
    bases = Bases(tmp_path)
    alone = ON_BASE.model_copy(update={"base": WorkspaceBase(image=IMAGE)})

    workspace = await bases.prepare(alone)

    assert bases.setup.ran == [] and await bases.kept() == []
    assert workspace.id not in bases.provider.bases


async def test_the_tenants_purge_removes_its_bases(tmp_path: Path) -> None:
    bases = Bases(tmp_path)
    other = context(Role.SERVICE, make_org())
    await bases.prepare()
    await bases.prepare(ctx=other)

    assert await bases.tools.manager.purge_tenant(bases.ctx) == 0, "a live tenant keeps them"
    bases.tools.members.expired = True
    assert await bases.tools.manager.purge_tenant(bases.ctx) == 1

    assert await bases.kept() == []
    assert await bases.kept(other) == [base_key(ON_BASE)], "another tenant's stay"
