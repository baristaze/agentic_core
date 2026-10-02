"""The transports, each over real processes in the workspace it serves: the
local one over a directory on this host, and the container one over a
container on the local Docker, which needs Docker and is skipped without it.

A secret injected into one process is redacted from everything it prints,
raw, encoded, and escaped, before any of it streams or returns, and the
process's environment holds nothing of the engine's. A command's whole tree
ends at its deadline. A command from a stale run is refused. How a command
ended is recorded under its key."""

import asyncio
import base64
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest

from acme.infra.base import new_id, utcnow
from acme.infra.exceptions import InfraNotFound
from acme.infra.secrets.local import SecretsLocalImpl
from acme.infra.transports import (
    CapabilityMissing,
    CommandSpec,
    PathOutsideWorkspace,
    SecretUse,
    SecretVia,
    StaleCommand,
    TransportInterface,
)
from acme.infra.transports.broker import BrokerNullImpl, BrokerTwinImpl
from acme.infra.transports.container import TransportContainerImpl
from acme.infra.transports.local import DEFAULT_PATH, TransportLocalImpl
from acme.infra.transports.redaction import forms, marker
from acme.infra.transports.twin import TransportNullImpl
from acme.infra.workspaces import (
    EgressMode,
    EgressPolicy,
    IsolationMode,
    IsolationSpec,
    Workspace,
    WorkspaceProviderInterface,
)
from acme.infra.workspaces.container import WorkspaceContainerImpl
from acme.infra.workspaces.host import WorkspaceHostImpl

SECRET = 'tok-3f9A/b+c="q"\\9z-0123456789'
TOKEN = SecretUse(name="api_token", via=SecretVia.INJECTED, env="API_TOKEN")
BROKERED = SecretUse(name="repo_token", via=SecretVia.BROKERED, destination="git.example.test")
ENGINE_CREDENTIAL = "ACME_DATABASE_URL"

PRINTS_THE_SECRET = r"""
import base64, json, os, sys, time, urllib.parse
token = os.environ["API_TOKEN"]
raw = token.encode()
print(token)
print(base64.b64encode(raw).decode())
print(base64.urlsafe_b64encode(raw).decode())
print(base64.b64encode(b"Authorization: Basic " + raw).decode())
print(raw.hex())
print(json.dumps({"token": token}))
print(urllib.parse.quote(token, safe=""))
print(repr(token))
print(token, file=sys.stderr)
sys.stdout.write("split:" + token[:7]); sys.stdout.flush(); time.sleep(0.3)
sys.stdout.write(token[7:] + "\n"); sys.stdout.flush()
print("names:" + ",".join(sorted(os.environ)))
"""

SPAWNS_A_TREE = r"""
import os, subprocess, time
def note(pid):
    with open("tree.pids", "a") as handle:
        handle.write(f"{pid} ")
note(os.getpid())
children = [
    subprocess.Popen(["sleep", "30"]),
    subprocess.Popen(["sh", "-c", "sleep 30 & echo $! >> tree.pids; sleep 30 & echo $! >> tree.pids; wait"]),
    subprocess.Popen(["sleep", "30"], start_new_session=True),
]
for child in children:
    note(child.pid)
time.sleep(0.5)
print("ready", flush=True)
time.sleep(60)
"""


def command(*argv: str, epoch: int = 1, seconds: float = 30, **fields: object) -> CommandSpec:
    return CommandSpec.model_validate(
        {
            "argv": argv,
            "key": new_id(),
            "epoch": epoch,
            "deadline": utcnow() + timedelta(seconds=seconds),
            **fields,
        }
    )


class TransportContract:
    """What every transport over real processes holds."""

    python = "python3"

    @pytest.fixture
    def transport(self) -> TransportInterface:
        raise NotImplementedError

    @pytest.fixture
    def workspace(self) -> Workspace:
        raise NotImplementedError

    async def test_a_command_answers_its_exit_and_its_output(
        self, transport: TransportInterface, workspace: Workspace
    ) -> None:
        result = await transport.run(
            workspace, command("sh", "-c", "echo out; echo err >&2; exit 3")
        )
        assert (result.exit_code, result.stdout, result.stderr) == (3, "out\n", "err\n")
        assert not result.timed_out and result.secrets == ()

    async def test_a_secret_is_redacted_in_every_form_before_it_streams_or_returns(
        self, transport: TransportInterface, workspace: Workspace
    ) -> None:
        parts: list[str] = []

        async def sink(stream: str, text: str) -> None:
            parts.append(text)

        result = await transport.run(
            workspace,
            command(self.python, "-c", PRINTS_THE_SECRET, secrets=(TOKEN,)),
            sink,
        )
        assert result.exit_code == 0, result.stderr
        assert result.secrets == ("api_token",)
        printed = result.stdout + result.stderr
        assert printed.count(marker("api_token")) >= 10
        for text in (printed, "".join(parts), *parts):
            for form in forms(SECRET):
                assert form not in text, form
        assert "split:" + marker("api_token") in result.stdout
        names = next(line for line in result.stdout.splitlines() if line.startswith("names:"))
        assert "API_TOKEN" in names and ENGINE_CREDENTIAL not in names

    async def test_a_brokered_secret_is_attached_and_never_injected(
        self, transport: TransportInterface, workspace: Workspace, broker: BrokerTwinImpl
    ) -> None:
        result = await transport.run(
            workspace,
            command(self.python, "-c", "import os; print(sorted(os.environ))", secrets=(BROKERED,)),
        )
        assert "repo_token" not in result.stdout.lower() and result.secrets == ("repo_token",)
        assert broker.attached == {} and broker.detached == [result.key]

    async def test_the_whole_tree_ends_at_the_deadline(
        self, transport: TransportInterface, workspace: Workspace
    ) -> None:
        started = utcnow()
        result = await transport.run(
            workspace, command(self.python, "-c", SPAWNS_A_TREE, seconds=3)
        )
        assert result.timed_out and result.exit_code is None
        assert "ready" in result.stdout
        assert utcnow() - started < timedelta(seconds=10)
        pids = (await transport.read_file(workspace, "tree.pids", 1000)).decode().split()
        assert len(pids) == 6, pids
        alive = await self._alive(transport, workspace, pids)
        assert alive == [], f"left running: {alive}"

    async def _alive(
        self, transport: TransportInterface, workspace: Workspace, pids: list[str]
    ) -> list[str]:
        """The pids still running; a killed process may linger a moment
        before its new parent reaps it."""
        check = 'for p in "$0" "$@"; do if kill -0 "$p" 2>/dev/null; then echo "$p"; fi; done'
        alive = pids
        for _ in range(30):
            found = await transport.run(workspace, command("sh", "-c", check, *pids))
            alive = found.stdout.split()
            if not alive:
                return []
            await asyncio.sleep(0.2)
        return alive

    async def test_a_stale_epoch_is_refused_and_runs_nothing(
        self, transport: TransportInterface, workspace: Workspace
    ) -> None:
        await transport.run(workspace, command("true", epoch=5))
        stale = command("sh", "-c", "touch ran", epoch=4)
        with pytest.raises(StaleCommand):
            await transport.run(workspace, stale)
        with pytest.raises(StaleCommand):
            await transport.write_file(workspace, "x", b"x", epoch=4)
        assert [entry.path for entry in await transport.list_files(workspace, ".", 10)] == []
        assert await transport.outcome(workspace, stale.key, epoch=5) is None
        with pytest.raises(StaleCommand):
            await transport.outcome(workspace, stale.key, epoch=4)

    async def test_how_a_command_ended_is_recorded_under_its_key(
        self, transport: TransportInterface, workspace: Workspace
    ) -> None:
        sent = command("sh", "-c", "echo done; exit 2")
        result = await transport.run(workspace, sent)
        assert await transport.outcome(workspace, sent.key, epoch=1) == result
        assert await transport.outcome(workspace, new_id(), epoch=1) is None

    async def test_asking_for_an_outcome_fences_the_run_it_replaces(
        self, transport: TransportInterface, workspace: Workspace
    ) -> None:
        lost = command("sh", "-c", "touch ran", epoch=1)
        assert await transport.outcome(workspace, lost.key, epoch=2) is None
        with pytest.raises(StaleCommand):
            await transport.run(workspace, lost)
        assert await transport.list_files(workspace, ".", 10) == []

    async def test_files_are_written_read_and_listed_inside_the_workspace(
        self, transport: TransportInterface, workspace: Workspace
    ) -> None:
        await transport.write_file(workspace, "src/app.py", b"print('hi')\n", epoch=1)
        assert await transport.read_file(workspace, "src/app.py", 5) == b"print"
        listed = await transport.list_files(workspace, ".", 10)
        assert [(entry.path, entry.is_dir) for entry in listed] == [("src", True)]
        (entry,) = await transport.list_files(workspace, "src", 10)
        assert (entry.path, entry.size) == ("src/app.py", 12)
        result = await transport.run(workspace, command("cat", "app.py", cwd="src"))
        assert result.stdout == "print('hi')\n"
        with pytest.raises(InfraNotFound):
            await transport.read_file(workspace, "missing.txt", 10)
        for outside in ("../x", "/etc/passwd", "src/../../x"):
            with pytest.raises(PathOutsideWorkspace):
                await transport.read_file(workspace, outside, 10)

    async def test_a_workspace_of_no_agent_or_another_mode_is_refused_loudly(
        self, transport: TransportInterface, workspace: Workspace
    ) -> None:
        absent = Workspace.absent(workspace.org_id, new_id())
        other = workspace.model_copy(
            update={
                "spec": IsolationSpec(
                    mode=IsolationMode.TWIN, egress=EgressPolicy(mode=EgressMode.NONE)
                )
            }
        )
        for refused in (absent, other):
            with pytest.raises(CapabilityMissing):
                await transport.run(refused, command("true"))
            with pytest.raises(CapabilityMissing):
                await transport.read_file(refused, "x", 1)


def secrets_for(org_id: UUID) -> SecretsLocalImpl:
    return SecretsLocalImpl(None, {f"{org_id.hex}_api_token".upper(): SECRET})


@pytest.fixture
def broker() -> BrokerTwinImpl:
    return BrokerTwinImpl()


@pytest.fixture(autouse=True)
def engine_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    """A credential of the engine's own, in the process that runs the
    transport, as a deployed process holds its database URL."""
    monkeypatch.setenv(ENGINE_CREDENTIAL, "postgresql://engine:not-for-tools@127.0.0.1/acme")


class TestTransportLocal(TransportContract):
    @pytest.fixture
    async def workspace(self, tmp_path: Path) -> Workspace:
        provider = WorkspaceHostImpl(tmp_path / "workspaces")
        spec = IsolationSpec(mode=IsolationMode.HOST, egress=EgressPolicy(mode=EgressMode.OPEN))
        return await provider.prepare(new_id(), new_id(), spec)

    @pytest.fixture
    def transport(
        self, tmp_path: Path, workspace: Workspace, broker: BrokerTwinImpl
    ) -> TransportInterface:
        python = Path(sys.executable).parent
        return TransportLocalImpl(
            tmp_path / "records",
            secrets_for(workspace.org_id),
            broker,
            search_path=f"{python}:{DEFAULT_PATH}",
        )

    async def test_a_link_out_of_the_workspace_is_refused(
        self, transport: TransportInterface, workspace: Workspace, tmp_path: Path
    ) -> None:
        (tmp_path / "outside.txt").write_text("the engine's")
        (Path(workspace.location) / "link").symlink_to(tmp_path / "outside.txt")
        with pytest.raises(PathOutsideWorkspace):
            await transport.read_file(workspace, "link", 100)

    async def test_with_no_broker_a_brokered_secret_refuses_the_command(
        self, tmp_path: Path, workspace: Workspace
    ) -> None:
        transport = TransportLocalImpl(
            tmp_path / "records", secrets_for(workspace.org_id), BrokerNullImpl()
        )
        sent = command("sh", "-c", "touch ran", secrets=(BROKERED,))
        with pytest.raises(CapabilityMissing):
            await transport.run(workspace, sent)
        assert not (Path(workspace.location) / "ran").exists()


async def test_the_null_transport_refuses_every_call() -> None:
    null = TransportNullImpl()
    workspace = Workspace.absent(new_id(), new_id())
    with pytest.raises(CapabilityMissing):
        await null.run(workspace, command("true"))
    with pytest.raises(CapabilityMissing):
        await null.outcome(workspace, new_id(), epoch=1)
    with pytest.raises(CapabilityMissing):
        await null.write_file(workspace, "x", b"", epoch=1)
    with pytest.raises(CapabilityMissing):
        await null.list_files(workspace, ".", 1)


def docker_runs() -> bool:
    try:
        reply = subprocess.run(["docker", "version"], capture_output=True, timeout=20)
    except FileNotFoundError, subprocess.TimeoutExpired:
        return False
    return reply.returncode == 0


@pytest.mark.integration
@pytest.mark.skipif(not docker_runs(), reason="needs a local Docker")
class TestTransportContainer(TransportContract):
    @pytest.fixture
    async def provided(self) -> AsyncIterator[tuple[WorkspaceProviderInterface, Workspace]]:
        provider = WorkspaceContainerImpl("python:3.14-slim", timedelta(seconds=300))
        spec = IsolationSpec(
            mode=IsolationMode.CONTAINER,
            egress=EgressPolicy(mode=EgressMode.NONE),
        )
        workspace = await provider.prepare(new_id(), new_id(), spec)
        yield provider, workspace
        await provider.purge(workspace)

    @pytest.fixture
    def workspace(self, provided: tuple[WorkspaceProviderInterface, Workspace]) -> Workspace:
        return provided[1]

    @pytest.fixture
    def transport(
        self, tmp_path: Path, workspace: Workspace, broker: BrokerTwinImpl
    ) -> TransportInterface:
        return TransportContainerImpl(
            tmp_path / "records", secrets_for(workspace.org_id), broker, timedelta(seconds=60)
        )

    async def test_no_egress_reaches_nothing(
        self, transport: TransportInterface, workspace: Workspace
    ) -> None:
        reach = "import socket; socket.create_connection(('1.1.1.1', 53), timeout=3)"
        result = await transport.run(workspace, command(self.python, "-c", reach))
        assert result.exit_code != 0

    async def test_a_released_workspace_keeps_its_files_for_the_next_instance(
        self,
        transport: TransportInterface,
        provided: tuple[WorkspaceProviderInterface, Workspace],
    ) -> None:
        provider, workspace = provided
        await transport.write_file(workspace, "kept.txt", b"kept", epoch=1)
        await provider.release(workspace)
        again = await provider.prepare(workspace.org_id, workspace.id, workspace.spec)
        assert await transport.read_file(again, "kept.txt", 10) == b"kept"


def test_a_secret_in_the_base64_of_a_basic_header_is_one_of_the_forms() -> None:
    """The fixture's script prints this one; the redactor knows it."""
    encoded = base64.b64encode(b"Authorization: Basic " + SECRET.encode()).decode()
    assert any(form in encoded for form in forms(SECRET))
