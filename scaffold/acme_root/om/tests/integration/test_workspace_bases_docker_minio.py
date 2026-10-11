"""Workspace bases on real Docker, kept on the stack's MinIO (ADR 1028). The
registry a setup reaches is a small HTTP server on this host, which counts
each request: the setup reaches it through the shared bridge, by the host's
name where Docker gives one and by the bridge's gateway where it does not.

A base is built once and every workspace on it starts from the same
snapshot; a changed command builds another. The setup reaches the registry
and the workspace, with no egress, does not. The setup's environment holds
no platform credential, no tenant's secret, and no session's content. A
setup command that fails keeps nothing, and the next prepare builds again.

Skipped, with the reason, where no Docker runs."""

import base64
import hashlib
import secrets as tokens
import subprocess
import threading
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import UUID

import aioboto3
import pytest
from contracts.loops import ASSISTANT, Loop, loop_over

from acme.infra.buckets import Buckets, BucketsInterface
from acme.infra.buckets.s3 import BucketsS3Impl
from acme.infra.docker import docker
from acme.infra.impl.local import InfraLocalImpl
from acme.infra.impl.settings import InfraSettings
from acme.infra.transports import TransportInterface
from acme.infra.transports.container import TransportContainerImpl
from acme.infra.workspaces import (
    BaseSetupFailed,
    EgressMode,
    EgressPolicy,
    IsolationMode,
    IsolationSpec,
    Workspace,
    WorkspaceBase,
    WorkspaceProviderInterface,
)
from acme.infra.workspaces.container import WorkspaceContainerImpl
from acme.om.tools.impl.bases import BUCKET, PREFIX, base_key

IMAGE = "python:3.14-slim"
TOKEN = "SERVICE_TOKEN"

REACH = r"""
import socket, sys, urllib.request
def gateway():
    for line in open("/proc/net/route").read().splitlines()[1:]:
        fields = line.split()
        if fields[1] == "00000000":
            return socket.inet_ntoa(int(fields[2], 16).to_bytes(4, "little"))
    return "unrouted"
port = int(sys.argv[1])
hosts = sys.argv[2:] or ["host.docker.internal", gateway()]
for host in hosts:
    try:
        urllib.request.urlopen(f"http://{host}:{port}/simple", timeout=5).read()
    except Exception as error:
        print(f"{host}: {error}", file=sys.stderr)
        continue
    open("/opt/registry", "w").write(host)
    sys.exit(0)
sys.exit(1)
"""
"""Reaches the registry on the port it is given, at the hosts it is given
or, with none, at the host's name and then at the bridge's gateway, and
writes down the host that answered."""


def reach(port: int) -> str:
    """The setup command that keeps `REACH` at /opt/reach.py and runs it."""
    script = base64.b64encode(REACH.encode()).decode()
    keep = f"import base64; open('/opt/reach.py', 'wb').write(base64.b64decode('{script}'))"
    return f'python -c "{keep}" && python /opt/reach.py {port}'


class Registry:
    """A registry on this host: it counts each request, and answers each
    with a server error while it is `failing`."""

    def __init__(self) -> None:
        self.hits = 0
        self.failing = False
        registry = self

        class Answer(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                registry.hits += 1
                self.send_response(500 if registry.failing else 200)
                self.end_headers()
                self.wfile.write(b"simple index")

            def log_message(self, format: str, *args: Any) -> None:
                return None

        # Every address of this host: a container reaches it through its bridge.
        self.server = ThreadingHTTPServer(("0.0.0.0", 0), Answer)
        self.port = self.server.server_address[1]


@pytest.fixture
def registry() -> Iterator[Registry]:
    served = Registry()
    thread = threading.Thread(target=served.server.serve_forever, daemon=True)
    thread.start()
    try:
        yield served
    finally:
        served.server.shutdown()
        served.server.server_close()


def docker_runs() -> bool:
    try:
        reply = subprocess.run(["docker", "version"], capture_output=True, timeout=20)
    except FileNotFoundError, subprocess.TimeoutExpired:
        return False
    return reply.returncode == 0


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_runs(), reason="needs a local Docker"),
    pytest.mark.usefixtures("migrated"),
]


class Spied(WorkspaceContainerImpl):
    """The container provider, with the hash of the base each workspace
    started from."""

    def __init__(self) -> None:
        super().__init__(IMAGE, timedelta(seconds=300))
        self.started_from: dict[UUID, str] = {}

    async def prepare(
        self,
        org_id: UUID,
        workspace_id: UUID,
        spec: IsolationSpec,
        snapshot: bytes | None = None,
        base: bytes | None = None,
    ) -> Workspace:
        if base is not None:
            self.started_from[workspace_id] = hashlib.sha256(base).hexdigest()
        return await super().prepare(org_id, workspace_id, spec, snapshot, base)


class InfraOnDocker(InfraLocalImpl):
    def __init__(self, root: Path, buckets: BucketsInterface) -> None:
        super().__init__(root)
        self.workspaces = Spied()
        self.transport = TransportContainerImpl(
            root / "records", self.get_secrets(), self.get_broker(), timedelta(seconds=120)
        )
        self.buckets = buckets

    def get_workspaces(self) -> WorkspaceProviderInterface:
        return self.workspaces

    def get_transport(self) -> TransportInterface:
        return self.transport

    def get_buckets(self) -> BucketsInterface:
        return self.buckets


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


class OnDocker:
    def __init__(self, tmp_path: Path, buckets: BucketsS3Impl) -> None:
        self.infra = InfraOnDocker(tmp_path, buckets)
        self.loop: Loop = loop_over(tmp_path, kinds=(ASSISTANT,), infra=self.infra)
        self.sessions: list[UUID] = []

    async def prepare(self, spec: IsolationSpec) -> Workspace:
        session_id = await self.loop.start(ASSISTANT.name)
        self.sessions.append(session_id)
        return await self.loop.managers.tools.prepare_workspace(self.loop.owner, session_id, spec)

    async def kept(self) -> list[str]:
        return await self.infra.buckets.list(self.loop.owner.org_id, BUCKET, PREFIX, 100)

    async def close(self) -> None:
        for session_id in self.sessions:
            await self.loop.managers.tools.purge_workspace(self.loop.owner.org_id, session_id)
        left = await docker(
            "ps",
            "--all",
            "--quiet",
            "--filter",
            f"label=acme.org={self.loop.owner.org_id}",
            bound=timedelta(seconds=20),
        )
        assert left.ok and not left.stdout.strip(), "no build's container is left"


@pytest.fixture
async def on_docker(tmp_path: Path, minio: BucketsS3Impl) -> AsyncIterator[OnDocker]:
    case = OnDocker(tmp_path, minio)
    try:
        yield case
    finally:
        await case.close()


async def inside(workspace: Workspace, *argv: str) -> tuple[int | None, str]:
    reply = await docker("exec", workspace.location, *argv, bound=timedelta(seconds=120))
    return reply.code, reply.stdout.decode()


def on_base(*setup: str) -> IsolationSpec:
    """A workspace with no egress, on a base whose setup has open egress."""
    base = WorkspaceBase(image=IMAGE, setup=setup, egress=EgressPolicy(mode=EgressMode.OPEN))
    return IsolationSpec(
        mode=IsolationMode.CONTAINER, egress=EgressPolicy(mode=EgressMode.NONE), base=base
    )


async def test_a_base_is_built_once_and_a_changed_one_is_built_again(
    on_docker: OnDocker, registry: Registry
) -> None:
    spec = on_base(reach(registry.port), "echo built >> /opt/count")

    first = await on_docker.prepare(spec)
    second = await on_docker.prepare(spec)

    assert registry.hits == 1, "the setup ran once"
    started = on_docker.infra.workspaces.started_from
    assert started[first.id] == started[second.id], "both start from the same snapshot"
    for workspace in (first, second):
        assert await inside(workspace, "cat", "/opt/count") == (0, "built\n")
    assert await on_docker.kept() == [base_key(spec)]

    changed = on_base(reach(registry.port), "echo changed >> /opt/count")
    third = await on_docker.prepare(changed)

    assert registry.hits == 2, "a changed command builds again"
    assert started[third.id] != started[first.id]
    assert await inside(third, "cat", "/opt/count") == (0, "changed\n")
    assert sorted(await on_docker.kept()) == sorted([base_key(spec), base_key(changed)])


async def test_the_setup_reaches_its_registry_and_the_workspace_does_not(
    on_docker: OnDocker, registry: Registry
) -> None:
    workspace = await on_docker.prepare(on_base(reach(registry.port)))
    assert registry.hits == 1, "the setup reached the registry"
    code, host = await inside(workspace, "cat", "/opt/registry")
    assert code == 0 and host

    code, _ = await inside(workspace, "python", "/opt/reach.py", str(registry.port), host)

    assert code == 1, "the workspace's call to the same address fails"
    assert registry.hits == 1, "nothing of it reached the registry"


async def test_the_setups_environment_holds_no_credential_and_no_session_content(
    on_docker: OnDocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    credential = f"cred-{tokens.token_hex(12)}"
    secret = f"tok-{tokens.token_hex(12)}"
    content = f"objective-{tokens.token_hex(12)}"
    monkeypatch.setenv("ACME_PLATFORM_CREDENTIAL", credential)
    loop = on_docker.loop
    await on_docker.infra.get_secrets().put(loop.owner.org_id, TOKEN, secret)
    session_id = await loop.start(ASSISTANT.name)
    on_docker.sessions.append(session_id)
    await loop.say(session_id, content)

    workspace = await loop.managers.tools.prepare_workspace(
        loop.owner, session_id, on_base("env > /opt/setup-env")
    )

    code, environment = await inside(workspace, "cat", "/opt/setup-env")
    assert code == 0 and "PATH=" in environment, "the image's own environment"
    for held in (credential, secret, content, "ACME_", TOKEN):
        assert held not in environment


async def test_a_failed_setup_keeps_nothing_and_the_next_prepare_builds_again(
    on_docker: OnDocker, registry: Registry
) -> None:
    registry.failing = True
    spec = on_base(reach(registry.port), "echo built >> /opt/count")

    with pytest.raises(BaseSetupFailed, match="setup command 1") as failed:
        await on_docker.prepare(spec)

    assert failed.value.exit_code == 1
    assert failed.value.command == reach(registry.port)
    assert await on_docker.kept() == [], "no half-built base"

    registry.failing = False
    workspace = await on_docker.prepare(spec)

    assert await inside(workspace, "cat", "/opt/count") == (0, "built\n")
    assert await on_docker.kept() == [base_key(spec)]
