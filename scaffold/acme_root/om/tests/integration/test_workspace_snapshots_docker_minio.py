"""Workspace snapshots on real Docker, kept on the local buckets and on the
stack's MinIO (ADR 1027). A snapshot is kept and named by a step, and a
restore from it gives the workspace back; one whose stored bytes were
altered is refused before any container starts. A credential the broker
attached, and a secret's variable, are never in what a snapshot keeps. A
child forked from its parent's snapshot sees the parent's files, and what
it writes never reaches the parent's workspace.

Skipped, with the reason, where no Docker runs."""

import secrets as tokens
import subprocess
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import aioboto3
import pytest
from contracts.loops import ASSISTANT, DELIVERY, Loop, loop_over
from contracts.tools import Command

from acme.infra.buckets import Buckets, BucketsInterface
from acme.infra.buckets.s3 import BucketsS3Impl
from acme.infra.docker import docker
from acme.infra.impl.local import InfraLocalImpl
from acme.infra.impl.settings import InfraSettings
from acme.infra.transports import (
    CommandSpec,
    CredentialBrokerInterface,
    SecretUse,
    SecretVia,
    TransportInterface,
)
from acme.infra.transports.broker import BrokerTwinImpl
from acme.infra.transports.container import TransportContainerImpl
from acme.infra.transports.twin import RecordSealTwin
from acme.infra.workspaces import (
    Durability,
    EgressMode,
    EgressPolicy,
    IsolationMode,
    IsolationSpec,
    SnapshotRefused,
    Workspace,
    WorkspaceLost,
    WorkspaceProviderInterface,
)
from acme.infra.workspaces.container import WorkspaceContainerImpl, container_name
from acme.om.agents.loop_rules import pending_restore
from acme.om.agents.types.request import Spawn
from acme.om.base import new_id, utcnow
from acme.om.steps.types.header import ControlHeader, SnapshotHeader, WorkspaceSnapshot
from acme.om.steps.types.step import Step
from acme.om.tools.impl.snapshots import BUCKET, snapshot_key
from acme.om.tools.native.spawn_sub_agent import SPAWN_SUB_AGENT

IMAGE = "python:3.14-slim"
CREDENTIALS = "/etc/acme-credentials"
TOKEN = "SERVICE_TOKEN"
INJECTED = SecretUse(name=TOKEN, via=SecretVia.INJECTED, env=TOKEN)
BROKERED = SecretUse(name="deploy-key", via=SecretVia.BROKERED, destination="git.example")
BOXED_SPEC = IsolationSpec(
    mode=IsolationMode.CONTAINER,
    egress=EgressPolicy(mode=EgressMode.NONE),
    durability=Durability.SNAPSHOT,
)
BOXED = ASSISTANT.model_copy(
    update={
        "name": "boxed",
        "isolation": BOXED_SPEC,
        "tools": (*ASSISTANT.tools, "run_command", SPAWN_SUB_AGENT),
    }
)


def docker_runs() -> bool:
    try:
        reply = subprocess.run(["docker", "version"], capture_output=True, timeout=20)
    except FileNotFoundError, subprocess.TimeoutExpired:
        return False
    return reply.returncode == 0


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_runs(), reason="needs a local Docker"),
    # The suite's schemas are made before any case's loop runs, so a run of
    # this file alone sets them up as the whole suite does.
    pytest.mark.usefixtures("migrated"),
]


async def inside(workspace: Workspace, *argv: str) -> tuple[int | None, str]:
    reply = await docker("exec", workspace.location, *argv, bound=timedelta(seconds=120))
    return reply.code, reply.stdout.decode()


class FileBroker(BrokerTwinImpl):
    """A broker that attaches a credential the way a credential helper does:
    a file in the workspace, there while the command that needs it runs."""

    async def attach(self, workspace: Workspace, key: UUID, use: SecretUse) -> None:
        await super().attach(workspace, key, use)
        held = f"mkdir -p {CREDENTIALS} && echo held > {CREDENTIALS}/{use.name}"
        code, _ = await inside(workspace, "sh", "-c", held)
        assert code == 0

    async def detach(self, workspace: Workspace, key: UUID) -> None:
        for use in self.attached.get(key, []):
            await inside(workspace, "rm", "-f", f"{CREDENTIALS}/{use.name}")
        await super().detach(workspace, key)


class InfraOnDocker(InfraLocalImpl):
    """The local root, with a container per workspace, its transport, the
    file broker, and the buckets the case names."""

    def __init__(self, root: Path, buckets: BucketsInterface | None = None) -> None:
        super().__init__(root)
        self.workspaces = WorkspaceContainerImpl(IMAGE, timedelta(seconds=300))
        self.broker = FileBroker()
        self.transport = TransportContainerImpl(
            root / "records", self.get_secrets(), self.broker, timedelta(seconds=120)
        )
        self.buckets = buckets or super().get_buckets()

    def get_workspaces(self) -> WorkspaceProviderInterface:
        return self.workspaces

    def get_transport(self) -> TransportInterface:
        return self.transport

    def get_broker(self) -> CredentialBrokerInterface:
        return self.broker

    def get_buckets(self) -> BucketsInterface:
        return self.buckets


def endpoint_of_stack() -> str:
    """The stack's MinIO, from .env: a second stack beside it has another port."""
    return InfraSettings().s3_endpoint_url or "http://127.0.0.1:59000"


@pytest.fixture
async def minio() -> AsyncIterator[BucketsS3Impl]:
    """The S3 impl over MinIO, on a snapshots bucket of its own, emptied and
    removed afterwards."""
    settings = InfraSettings()
    session = aioboto3.Session(
        aws_access_key_id=settings.s3_access_key or "acme",
        aws_secret_access_key=settings.s3_secret_key or "acme-minio-local",
        region_name=settings.aws_region,
    )
    endpoint = endpoint_of_stack()
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


def on_docker(
    tmp_path: Path, buckets: BucketsInterface | None = None
) -> tuple[Loop, InfraOnDocker]:
    infra = InfraOnDocker(tmp_path, buckets)
    loop = loop_over(
        tmp_path,
        kinds=(ASSISTANT, DELIVERY, BOXED),
        infra=infra,
        extra=(Command("run_command", secrets=(INJECTED, BROKERED)),),
    )
    return loop, infra


async def snapshot_of(loop: Loop, session_id: UUID, workspace: Workspace) -> Step:
    epoch = await loop.managers.steps.begin_run(loop.owner, session_id)
    return await loop.managers.tools.snapshot_workspace(
        loop.owner, session_id, workspace, epoch=epoch, loop_id=new_id()
    )


def named(step: Step) -> WorkspaceSnapshot:
    assert isinstance(step.header, SnapshotHeader)
    return step.header.snapshot


async def container_runs(workspace_id: UUID) -> bool:
    """Whether a container stands for the workspace; its volume, under the
    same name, outlives a release."""
    name = container_name(workspace_id)
    shown = await docker("inspect", "--type", "container", name, bound=timedelta(seconds=20))
    return shown.ok


async def restores_and_refuses_an_altered_one(
    tmp_path: Path, buckets: BucketsInterface | None
) -> None:
    """A snapshot the step names restores the workspace it was taken of; once
    its stored bytes are altered, the restore is refused and no container
    starts."""
    loop, infra = on_docker(tmp_path, buckets)
    tools, ctx = loop.managers.tools, loop.owner
    session_id = await loop.start(BOXED.name)
    workspace = await tools.prepare_workspace(ctx, session_id, BOXED_SPEC)
    try:
        await inside(workspace, "sh", "-c", "echo kept > /workspace/a.txt && echo kept > /opt/b")
        step = await snapshot_of(loop, session_id, workspace)
        # The step the history names is what the restore reads.
        (read,) = [s for s in await loop.history(session_id) if s.id == step.id]
        snapshot = named(read)
        await inside(workspace, "sh", "-c", "echo later > /workspace/a.txt && rm /opt/b")
        await tools.release_workspace(ctx, workspace)

        restored = await tools.prepare_workspace(ctx, session_id, BOXED_SPEC, restore=snapshot)
        assert await inside(restored, "cat", "/workspace/a.txt", "/opt/b") == (0, "kept\nkept\n")

        await tools.release_workspace(ctx, restored)
        key = snapshot_key(session_id, snapshot.hash)
        sealed = bytearray(await infra.buckets.get(ctx.org_id, BUCKET, key))
        sealed[len(sealed) // 2] ^= 0x01
        await infra.buckets.put(ctx.org_id, BUCKET, key, bytes(sealed), "application/octet-stream")
        with pytest.raises(WorkspaceLost, match="altered"):
            await tools.prepare_workspace(ctx, session_id, BOXED_SPEC, restore=snapshot)
        assert not await container_runs(session_id), "no workspace started"
    finally:
        await tools.purge_workspace(ctx.org_id, session_id)


async def test_a_snapshot_on_the_local_buckets_restores_and_an_altered_one_starts_nothing(
    tmp_path: Path,
) -> None:
    await restores_and_refuses_an_altered_one(tmp_path, None)


async def test_a_snapshot_on_minio_restores_and_an_altered_one_starts_nothing(
    tmp_path: Path, minio: BucketsS3Impl
) -> None:
    await restores_and_refuses_an_altered_one(tmp_path, minio)


async def test_a_snapshot_holds_no_credential_the_broker_attached_and_no_secrets_variable(
    tmp_path: Path,
) -> None:
    """While a command runs, its brokered credential is a file there and its
    injected secret a variable of its process. A snapshot taken with a
    credential still attached, as a lost run leaves one, takes it back
    first: the workspace restored from it holds neither. A workspace a
    command wrote the secret's value into keeps no snapshot."""
    loop, infra = on_docker(tmp_path)
    tools, ctx = loop.managers.tools, loop.owner
    await infra.get_secrets().put(ctx.org_id, TOKEN, f"tok-{tokens.token_hex(12)}")
    session_id = await loop.start(BOXED.name)
    workspace = await tools.prepare_workspace(ctx, session_id, BOXED_SPEC)
    seal = RecordSealTwin().seal

    def command(script: str) -> CommandSpec:
        return CommandSpec(
            argv=("sh", "-c", script),
            secrets=(BROKERED, INJECTED),
            key=new_id(),
            epoch=1,
            deadline=utcnow() + timedelta(minutes=1),
        )

    credential = f"{CREDENTIALS}/{BROKERED.name}"
    seen = f'test -e {credential} && test -n "${TOKEN}" && echo both'
    try:
        ran = await infra.transport.run(workspace, command(seen), seal=seal)
        assert ran.stdout.strip() == "both", "both are there while the command runs"
        # A run lost while its command held the credential never took it back.
        await infra.broker.attach(workspace, new_id(), BROKERED)
        assert (await inside(workspace, "test", "-e", credential))[0] == 0

        snapshot = named(await snapshot_of(loop, session_id, workspace))
        await tools.release_workspace(ctx, workspace)
        restored = await tools.prepare_workspace(ctx, session_id, BOXED_SPEC, restore=snapshot)

        assert (await inside(restored, "test", "-e", credential))[0] == 1, "no credential"
        assert (await inside(restored, "printenv", TOKEN))[0] == 1, "no secret's variable"

        leak = await infra.transport.run(
            restored, command(f'echo "${TOKEN}" > /workspace/leak.txt'), seal=seal
        )
        assert leak.exit_code == 0
        with pytest.raises(SnapshotRefused, match=TOKEN):
            await snapshot_of(loop, session_id, restored)
    finally:
        await tools.purge_workspace(ctx.org_id, session_id)


async def test_a_child_forked_from_its_parents_snapshot_sees_its_files_and_never_writes_back(
    tmp_path: Path,
) -> None:
    loop, _ = on_docker(tmp_path)
    tools, ctx = loop.managers.tools, loop.owner
    parent = await loop.start(BOXED.name)
    child_id: UUID | None = None
    parents = await tools.prepare_workspace(ctx, parent, BOXED_SPEC)
    try:
        wrote = "echo parent > /workspace/parent.txt && echo parent > /opt/parent.txt"
        await inside(parents, "sh", "-c", wrote)
        await snapshot_of(loop, parent, parents)
        asked = Spawn(
            id=new_id(), kind=BOXED.name, title="a try", objective="Try it on a copy.", fork=True
        )
        child = await loop.managers.agents.spawn(ctx, parent, asked)
        child_id = child.id
        # The child's loop prepares from the restore its history holds.
        restore = pending_restore(await loop.history(child.id))
        assert restore is not None and isinstance(restore.header, ControlHeader)
        childs = await tools.prepare_workspace(
            ctx, child.id, BOXED_SPEC, restore=restore.header.snapshot
        )

        seen = await inside(childs, "cat", "/workspace/parent.txt", "/opt/parent.txt")
        assert seen == (0, "parent\nparent\n"), "the child sees the parent's files"
        await inside(childs, "sh", "-c", "echo child > /workspace/own.txt && echo c > /opt/own")
        for path in ("/workspace/own.txt", "/opt/own"):
            assert (await inside(parents, "test", "-e", path))[0] == 1, f"{path} stays the child's"
    finally:
        await tools.purge_workspace(ctx.org_id, parent)
        if child_id is not None:
            await tools.purge_workspace(ctx.org_id, child_id)
