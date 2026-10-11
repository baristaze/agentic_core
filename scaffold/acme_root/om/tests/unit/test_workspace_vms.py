"""A VM workspace's snapshots and bases, through the tools, over the VM
provider and its transport on the machines' twin (ADR 1029). A snapshot of a
VM is its disk, kept by the machines: the archive the history names holds a
name and a digest, and the scan for a secret's value reads the disk. A
fork's copy is the child's own disk, and outlives its parent's purge. A
base's disk is kept under the tenant, outlives the build that made it, and
goes with the tenant's purge."""

from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from contracts.doubles import context
from contracts.factories import make_org
from contracts.tools import Tools, tools_over

from acme.infra.buckets.local import BucketsLocalImpl
from acme.infra.machines.twin import MachinesTwinImpl
from acme.infra.secrets.local import SecretsLocalImpl
from acme.infra.transports.broker import BrokerTwinImpl
from acme.infra.transports.vm import TransportVmImpl
from acme.infra.workspaces import (
    Durability,
    EgressMode,
    EgressPolicy,
    IsolationMode,
    IsolationSpec,
    SnapshotRefused,
    Workspace,
    WorkspaceBase,
)
from acme.infra.workspaces.vm import WORKSPACE, WorkspaceVmImpl, machine_name
from acme.om.base import new_id
from acme.om.context import Role
from acme.om.steps.types.header import SnapshotHeader, WorkspaceSnapshot
from acme.om.steps.types.step import Step
from acme.om.tools.impl.bases import base_key, base_workspace_id

PREFIX = "acme-test-"
KEPT = IsolationSpec(
    mode=IsolationMode.VM,
    egress=EgressPolicy(mode=EgressMode.OPEN),
    durability=Durability.SNAPSHOT,
)
ON_BASE = IsolationSpec(
    mode=IsolationMode.VM,
    egress=EgressPolicy(mode=EgressMode.OPEN),
    base=WorkspaceBase(image="twin-image", setup=("echo from-the-base > base.txt",)),
)


class Vms:
    """The tools over the VM provider on the machines' twin, with one
    injected secret the catalog may give a command."""

    def __init__(self, tmp_path: Path) -> None:
        self.machines = MachinesTwinImpl(tmp_path / "machines")
        self.provider = WorkspaceVmImpl(self.machines, "twin-image", PREFIX, timedelta(seconds=60))
        self.secrets = SecretsLocalImpl(tmp_path / "secrets.env")
        transport = TransportVmImpl(
            tmp_path / "records",
            self.secrets,
            BrokerTwinImpl(),
            self.machines,
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

    def folder(self, workspace_id: UUID) -> Path:
        """The machine's root, on the twin."""
        return self.machines.machines[machine_name(PREFIX, workspace_id)].folder

    async def snapshot(self, workspace: Workspace) -> WorkspaceSnapshot:
        epoch = await self.tools.steps.begin_run(self.ctx, workspace.id)
        step = await self.tools.manager.snapshot_workspace(
            self.ctx, workspace.id, workspace, epoch=epoch, loop_id=new_id()
        )
        return named(step)


def named(step: Step) -> WorkspaceSnapshot:
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
    session_id = new_id()
    workspace = await vms.tools.manager.prepare_workspace(vms.ctx, session_id, KEPT)
    (vms.folder(session_id) / WORKSPACE / "notes.txt").write_text("clean")

    snapshot = await vms.snapshot(workspace)
    assert snapshot.size < 512, "a name and a digest, never the disk"

    (vms.folder(session_id) / "etc-profile").write_text("export T=tok-0123456789abcdef\n")
    with pytest.raises(SnapshotRefused, match="SERVICE_TOKEN"):
        await vms.snapshot(workspace)
    disks = await vms.machines.names(f"{machine_name(PREFIX, session_id)}-")
    assert len(disks) == 1, "the refused snapshot's disk went; the kept one stays"


async def test_a_fork_of_a_vm_snapshot_is_the_childs_own_disk(tmp_path: Path) -> None:
    vms = Vms(tmp_path)
    parent, child = new_id(), new_id()
    workspace = await vms.tools.manager.prepare_workspace(vms.ctx, parent, KEPT)
    (vms.folder(parent) / WORKSPACE / "state.txt").write_text("the parent's")
    source = await vms.snapshot(workspace)

    copy = await vms.tools.manager.fork_snapshot(vms.ctx, parent, child, new_id(), source)
    await vms.tools.manager.purge_workspace(vms.ctx.org_id, parent)

    assert await vms.machines.names(machine_name(PREFIX, parent)) == []
    forked = await vms.tools.manager.prepare_workspace(vms.ctx, child, KEPT, restore=copy)
    assert (vms.folder(forked.id) / WORKSPACE / "state.txt").read_text() == "the parent's"


async def test_a_vm_base_outlives_its_build_and_goes_with_the_tenants_purge(
    tmp_path: Path,
) -> None:
    vms = Vms(tmp_path)
    kept_under = machine_name(PREFIX, base_workspace_id(vms.ctx.org_id, base_key(ON_BASE)))

    session_id = new_id()
    await vms.tools.manager.prepare_workspace(vms.ctx, session_id, ON_BASE)

    built = (vms.folder(session_id) / WORKSPACE / "base.txt").read_text()
    assert built == "from-the-base\n"
    (base,) = [name for name in vms.machines.machines if name.startswith(kept_under)]
    assert set(vms.machines.machines) == {base, machine_name(PREFIX, session_id)}, (
        "the build's own machine went"
    )
    vms.tools.members.expired = True
    assert await vms.tools.manager.purge_tenant(vms.ctx) == 1
    assert await vms.machines.names(kept_under) == []
