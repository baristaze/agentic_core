"""A VM workspace's snapshots and bases, through the tools, over the VM
provider and its transport on the VMs' twin (ADR 1029). A snapshot of a
VM is its disk, kept by the VMs: the archive the history names holds a
name and a digest, and the scan for a secret's value reads the disk. A
fork's copy is the child's own disk, and outlives its parent's purge; a
cache parent keeps no disk of what the spawn took. A revocation of a
session's key destroys the disks kept for its snapshots, and no other
session's. A base's disk is kept under the tenant, outlives the build that
made it, and goes with the tenant's purge."""

from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest

from acme.infra.agent_workspaces import (
    AgentWorkspace,
    AgentWorkspaceBase,
    AgentWorkspaceProviderInterface,
    Durability,
    EgressMode,
    EgressPolicy,
    IsolationMode,
    IsolationSpec,
    SnapshotRefused,
)
from acme.infra.agent_workspaces.vm import WORKSPACE, AgentWorkspaceVmImpl, vm_name
from acme.infra.buckets.local import BucketsLocalImpl
from acme.infra.impl.local import InfraLocalImpl
from acme.infra.secrets.local import SecretsLocalImpl
from acme.infra.transports.broker import BrokerTwinImpl
from acme.infra.transports.vm import TransportVmImpl
from acme.infra.vms.twin import VmsTwinImpl
from acme.om.agent_root import AgentRootOptions
from acme.om.agent_steps.types.agent_step import AgentStep
from acme.om.agent_steps.types.header import AgentWorkspaceSnapshot, SnapshotHeader
from acme.om.base import new_id
from acme.om.context import Role
from acme.om.root import build_managers
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.om.tools.impl.bases import base_agent_workspace_id, base_key
from acme.om.tools.tool import TakenSnapshot

from contracts.agent_session_storage import make_agent_session
from contracts.doubles import context
from contracts.factories import make_org
from contracts.tools import Tools, stand_ins, tools_over

PREFIX = "acme-test-"
KEPT = IsolationSpec(
    mode=IsolationMode.VM,
    egress=EgressPolicy(mode=EgressMode.OPEN),
    durability=Durability.SNAPSHOT,
)
ON_BASE = IsolationSpec(
    mode=IsolationMode.VM,
    egress=EgressPolicy(mode=EgressMode.OPEN),
    base=AgentWorkspaceBase(image="twin-image", setup=("echo from-the-base > base.txt",)),
)


class Vms:
    """The tools over the VM provider on the VMs' twin, with one
    injected secret the catalog may give a command."""

    def __init__(self, tmp_path: Path) -> None:
        self.vm_twin = VmsTwinImpl(tmp_path / "vms")
        self.provider = AgentWorkspaceVmImpl(
            self.vm_twin, "twin-image", PREFIX, timedelta(seconds=60)
        )
        self.secrets = SecretsLocalImpl(tmp_path / "secrets.env")
        transport = TransportVmImpl(
            tmp_path / "records",
            self.secrets,
            BrokerTwinImpl(),
            self.vm_twin,
            timedelta(seconds=60),
        )
        self.tools: Tools = tools_over(
            transport,
            self.provider,
            buckets=BucketsLocalImpl(tmp_path / "buckets"),
            secrets=self.secrets,
            secret_names=frozenset({"SERVICE_TOKEN"}),
        )
        self.ctx = context(Role.SERVICE, make_org())

    def folder(self, agent_workspace_id: UUID) -> Path:
        """The VM's root, on the twin."""
        return self.vm_twin.vms[vm_name(PREFIX, agent_workspace_id)].folder

    async def snapshot(self, workspace: AgentWorkspace) -> AgentWorkspaceSnapshot:
        epoch = await self.tools.steps.begin_run(self.ctx, workspace.id)
        step = await self.tools.manager.snapshot_agent_workspace(
            self.ctx, workspace.id, workspace, epoch=epoch, loop_id=new_id(), tools=()
        )
        return named(step)


def named(step: AgentStep) -> AgentWorkspaceSnapshot:
    assert isinstance(step.header, SnapshotHeader)
    return step.header.snapshot


async def test_a_vm_snapshot_names_its_disk_and_the_scan_reads_the_disk(
    tmp_path: Path,
) -> None:
    """The step names an archive of a few hundred bytes. A secret's value a
    command wrote anywhere on the disk, outside the workspace's folder
    included, refuses the next snapshot, by the secret's name, and its disk
    is not kept."""
    vms = Vms(tmp_path)
    await vms.secrets.put(vms.ctx.org_id, "SERVICE_TOKEN", "tok-0123456789abcdef")
    agent_session_id = new_id()
    workspace = await vms.tools.manager.prepare_agent_workspace(vms.ctx, agent_session_id, KEPT)
    (vms.folder(agent_session_id) / WORKSPACE / "notes.txt").write_text("clean")

    snapshot = await vms.snapshot(workspace)
    assert snapshot.size < 512, "a name and a digest, never the disk"

    (vms.folder(agent_session_id) / "etc-profile").write_text("export T=tok-0123456789abcdef\n")
    with pytest.raises(SnapshotRefused, match="SERVICE_TOKEN"):
        await vms.snapshot(workspace)
    disks = await vms.vm_twin.names(f"{vm_name(PREFIX, agent_session_id)}-")
    assert len(disks) == 1, "the refused snapshot's disk went; the kept one stays"


async def test_a_fork_of_a_vm_is_the_childs_own_disk(tmp_path: Path) -> None:
    """The child's copy of what the spawn took is a disk under its own
    workspace, so its parent's purge leaves it whole. A parent kept by
    snapshots keeps the disk it took; a cache keeps none."""
    vms = Vms(tmp_path)
    for durability in (Durability.SNAPSHOT, Durability.CACHE):
        parent, child = new_id(), new_id()
        spec = KEPT.model_copy(update={"durability": durability})
        workspace = await vms.tools.manager.prepare_agent_workspace(vms.ctx, parent, spec)
        (vms.folder(parent) / WORKSPACE / "state.txt").write_text("the parent's")
        kept = durability is Durability.SNAPSHOT
        taken = TakenSnapshot(
            agent_workspace_id=parent, archive=await vms.provider.snapshot(workspace), kept=kept
        )

        copy = await vms.tools.manager.fork_snapshot(vms.ctx, child, new_id(), taken)

        parents = await vms.vm_twin.names(f"{vm_name(PREFIX, parent)}-")
        assert len(parents) == (1 if kept else 0), durability
        await vms.tools.manager.purge_agent_workspace(vms.ctx.org_id, parent)
        assert await vms.vm_twin.names(vm_name(PREFIX, parent)) == []
        forked = await vms.tools.manager.prepare_agent_workspace(vms.ctx, child, KEPT, restore=copy)
        assert (vms.folder(forked.id) / WORKSPACE / "state.txt").read_text() == "the parent's"


async def test_a_vm_base_outlives_its_build_and_goes_with_the_tenants_purge(
    tmp_path: Path,
) -> None:
    vms = Vms(tmp_path)
    kept_under = vm_name(PREFIX, base_agent_workspace_id(vms.ctx.org_id, base_key(ON_BASE)))

    agent_session_id = new_id()
    await vms.tools.manager.prepare_agent_workspace(vms.ctx, agent_session_id, ON_BASE)

    built = (vms.folder(agent_session_id) / WORKSPACE / "base.txt").read_text()
    assert built == "from-the-base\n"
    (base,) = [name for name in vms.vm_twin.vms if name.startswith(kept_under)]
    assert set(vms.vm_twin.vms) == {base, vm_name(PREFIX, agent_session_id)}, (
        "the build's own VM went"
    )
    vms.tools.members.expired = True
    assert await vms.tools.manager.purge_tenant(vms.ctx) == 1
    assert await vms.vm_twin.names(kept_under) == []


class VmInfra(InfraLocalImpl):
    """The local infra root, its workspaces VMs on its VMs' twin."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        vm_twin = self.get_vms()
        assert isinstance(vm_twin, VmsTwinImpl)
        self.vm_twin = vm_twin
        self.vms = AgentWorkspaceVmImpl(vm_twin, "twin-image", PREFIX, timedelta(seconds=60))

    def get_agent_workspaces(self) -> AgentWorkspaceProviderInterface:
        return self.vms


async def test_a_revoked_key_destroys_the_disks_kept_for_its_snapshots_and_no_other_sessions(
    tmp_path: Path,
) -> None:
    """A VM's disks are kept outside the seal, so a revocation of the
    session's key destroys every disk kept for its snapshots with it. The
    workspace, a cache, stays, and so does a fork's copy, kept for the
    child under its own key: the child still starts from it."""
    infra = VmInfra(tmp_path)
    managers = build_managers(
        StorageMemoryImpl(),
        infra,
        agent_root_options=AgentRootOptions(tool_catalog=stand_ins("read_log")),
    )
    ctx = context(Role.MEMBER)
    parent, child = [
        (await managers.agent_sessions.create_agent_session(ctx, make_agent_session())).id
        for _ in range(2)
    ]
    workspace = await managers.tools.prepare_agent_workspace(ctx, parent, KEPT)
    folder = infra.vm_twin.vms[vm_name(PREFIX, parent)].folder / WORKSPACE
    (folder / "state.txt").write_text("the parent's")
    epoch = await managers.agent_steps.begin_run(ctx, parent)
    for _ in range(2):
        await managers.tools.snapshot_agent_workspace(
            ctx, parent, workspace, epoch=epoch, loop_id=new_id(), tools=()
        )
    taken = TakenSnapshot(agent_workspace_id=parent, archive=await infra.vms.snapshot(workspace))
    copy = await managers.tools.fork_snapshot(ctx, child, new_id(), taken)
    kept_for = {
        session: await infra.vm_twin.names(f"{vm_name(PREFIX, session)}-")
        for session in (parent, child)
    }
    assert (len(kept_for[parent]), len(kept_for[child])) == (2, 1)

    await managers.privacy.revoke_key(ctx, parent)

    assert await infra.vm_twin.names(f"{vm_name(PREFIX, parent)}-") == []
    assert await infra.vm_twin.names(vm_name(PREFIX, parent)) == [vm_name(PREFIX, parent)], (
        "the workspace stays"
    )
    assert await infra.vm_twin.names(f"{vm_name(PREFIX, child)}-") == kept_for[child]
    await managers.tools.prepare_agent_workspace(ctx, child, KEPT, restore=copy)
    forked = infra.vm_twin.vms[vm_name(PREFIX, child)].folder / WORKSPACE
    assert (forked / "state.txt").read_text() == "the parent's"
