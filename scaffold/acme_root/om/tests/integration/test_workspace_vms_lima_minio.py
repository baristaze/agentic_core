"""A VM workspace's snapshot through the tools, on a machine on Lima, its
archive kept on the stack's MinIO (ADR 1029). The step names an archive of
a few hundred bytes, sealed in the bucket; the disk stays with the
machines. Prepared from it after its machine is destroyed, the workspace
answers the same file and the same Docker image. A secret's value a
command wrote anywhere on the disk refuses the next snapshot, by the
secret's name, and nothing more is stored.

Skipped, with the reason, where Lima or a hypervisor is missing. The
machine is named under `ACME_MACHINE_PREFIX` and destroyed at the end."""

import os
import secrets as tokens
import shutil
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import aioboto3
import pytest
from contracts.doubles import context
from contracts.factories import make_org
from contracts.tools import tools_over

from acme.infra.base import utcnow
from acme.infra.buckets import Buckets
from acme.infra.buckets.s3 import BucketsS3Impl
from acme.infra.impl.settings import InfraSettings
from acme.infra.machines import MachineState
from acme.infra.machines.lima import MachinesLimaImpl, hypervisor
from acme.infra.secrets.local import SecretsLocalImpl
from acme.infra.transports import CommandSpec
from acme.infra.transports.broker import BrokerTwinImpl
from acme.infra.transports.twin import RecordSealTwin
from acme.infra.transports.vm import TransportVmImpl
from acme.infra.workspaces import (
    Durability,
    EgressMode,
    EgressPolicy,
    IsolationMode,
    IsolationSpec,
    SnapshotRefused,
    Workspace,
)
from acme.infra.workspaces.vm import WorkspaceVmImpl
from acme.om.base import new_id
from acme.om.context import Role
from acme.om.steps.types.header import SnapshotHeader
from acme.om.tools.impl.snapshots import session_prefix

KEPT = IsolationSpec(
    mode=IsolationMode.VM,
    egress=EgressPolicy(mode=EgressMode.OPEN),
    durability=Durability.SNAPSHOT,
)
SEAL = RecordSealTwin().seal
TIMEOUT = timedelta(seconds=300)


def lima_runs() -> bool:
    return shutil.which("limactl") is not None and hypervisor() is None


pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(not lima_runs(), reason="needs Lima and a hypervisor"),
    # The suite's schemas are made before any case runs, so a run of this
    # file alone sets them up as the whole suite does.
    pytest.mark.usefixtures("migrated"),
]


@pytest.fixture
async def minio() -> AsyncIterator[BucketsS3Impl]:
    """The S3 impl over the stack's MinIO, on a snapshots bucket of its own,
    emptied and removed afterwards."""
    settings = InfraSettings()
    session = aioboto3.Session(
        aws_access_key_id=settings.s3_access_key or "acme",
        aws_secret_access_key=settings.s3_secret_key or "acme-minio-local",
        region_name=settings.aws_region,
    )
    endpoint = settings.s3_endpoint_url or "http://127.0.0.1:59000"
    prefix = f"acme-it-{tokens.token_hex(4)}"
    name = f"{prefix}-{Buckets.SNAPSHOTS.value}"
    admin: Any = session.client("s3", endpoint_url=endpoint)
    async with admin as s3:
        await s3.create_bucket(Bucket=name)
    impl = BucketsS3Impl(
        session,
        endpoint_url=endpoint,
        region=settings.aws_region,
        bucket_prefix=prefix,
        timeout=timedelta(seconds=30),
    )
    await impl.start()
    try:
        yield impl
    finally:
        await impl.close()
        cleanup: Any = session.client("s3", endpoint_url=endpoint)
        async with cleanup as s3:
            listed = await s3.list_objects_v2(Bucket=name)
            for item in listed.get("Contents", []):
                await s3.delete_object(Bucket=name, Key=item["Key"])
            await s3.delete_bucket(Bucket=name)


async def run(transport: TransportVmImpl, workspace: Workspace, script: str) -> str:
    sent = CommandSpec(
        argv=("sh", "-c", script), key=new_id(), epoch=1, deadline=utcnow() + TIMEOUT
    )
    ran = await transport.run(workspace, sent, seal=SEAL)
    assert ran.exit_code == 0, ran.stdout + ran.stderr
    return ran.stdout


async def test_a_vm_snapshot_is_kept_on_minio_by_name_and_restores_its_whole_disk(
    tmp_path: Path, minio: BucketsS3Impl
) -> None:
    machines = MachinesLimaImpl(TIMEOUT, timedelta(minutes=15), encrypted=True)
    prefix = os.environ.get("ACME_MACHINE_PREFIX", "acme-test-")
    provider = WorkspaceVmImpl(machines, "template:_images/ubuntu-lts", prefix, TIMEOUT)
    secrets = SecretsLocalImpl(tmp_path / "secrets.env")
    transport = TransportVmImpl(tmp_path / "records", secrets, BrokerTwinImpl(), machines, TIMEOUT)
    tools = tools_over(
        transport,
        provider,
        buckets=minio,
        secrets=secrets,
        secret_names=frozenset({"SERVICE_TOKEN"}),
    )
    ctx = context(Role.SERVICE, make_org())
    await secrets.put(ctx.org_id, "SERVICE_TOKEN", "tok-vm-0123456789abcdef")
    session_id = new_id()
    workspace = await tools.manager.prepare_workspace(ctx, session_id, KEPT)
    try:
        before = await run(
            transport,
            workspace,
            "head -c 4096 /dev/urandom > state.bin && mkdir -p ctx && cp state.bin ctx/ && "
            "printf 'FROM scratch\\nCOPY state.bin /\\n' > ctx/Dockerfile && "
            "docker build -q -t acme-test-kept ctx >/dev/null && sha256sum state.bin && "
            "docker image inspect --format '{{.Id}}' acme-test-kept",
        )
        epoch = await tools.steps.begin_run(ctx, session_id)
        step = await tools.manager.snapshot_workspace(
            ctx, session_id, workspace, epoch=epoch, loop_id=new_id()
        )
        assert isinstance(step.header, SnapshotHeader)
        snapshot = step.header.snapshot
        (key,) = await minio.list(ctx.org_id, Buckets.SNAPSHOTS, session_prefix(session_id), 10)
        sealed = await minio.get(ctx.org_id, Buckets.SNAPSHOTS, key)
        assert snapshot.size < 512 and len(sealed) < 1024, "a name and a digest, never the disk"
        assert workspace.location.encode() not in sealed, "sealed, never in the clear"

        await machines.destroy(workspace.location)
        assert await machines.state(workspace.location) is MachineState.ABSENT
        restored = await tools.manager.prepare_workspace(ctx, session_id, KEPT, restore=snapshot)
        check = "sha256sum state.bin && docker image inspect --format '{{.Id}}' acme-test-kept"
        assert await run(transport, restored, check) == before

        await run(transport, restored, "echo tok-vm-0123456789abcdef | sudo tee /etc/leak")
        with pytest.raises(SnapshotRefused, match="SERVICE_TOKEN"):
            await tools.manager.snapshot_workspace(
                ctx, session_id, restored, epoch=epoch, loop_id=new_id()
            )
        kept = await minio.list(ctx.org_id, Buckets.SNAPSHOTS, session_prefix(session_id), 10)
        assert kept == [key], "nothing more stored"
        disks = await machines.names(f"{workspace.location}-")
        assert len(disks) == 1, "the refused snapshot's disk went; the kept one stays"
    finally:
        await tools.manager.purge_workspace(ctx.org_id, session_id)
    assert await machines.names(workspace.location) == [], "the machine and its disks"
