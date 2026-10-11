"""The transports, each over real processes in the workspace it serves: the
local one over a directory on this host, the container one over a
container on the local Docker, which needs Docker and is skipped without it,
and the VM one over a machine of the machines' twin and, where this host
runs Lima, over a machine on Lima, skipped without it.

A secret injected into one process is redacted from everything it prints,
raw, encoded, and escaped, before any of it streams or returns, and the
process's environment holds nothing of the engine's. A command's whole tree
ends at its deadline. An output past its bound keeps its head and its tail,
and a viewer that fails never stops the command. A command from a stale run
is refused. How a command ended is recorded under its key, its output sealed
by the seal it came with, and the records go when they are purged. A file
is read from an offset, with only what follows it answered.

On this host, a command is over when its own process exits: a process it
left holding its output is ended once the output has drained for its bound.
A file is read from its offset without a byte before it read, and never
through a link. The local transport given an account holds the same over
the account mode, where the host can switch to the account
TEST_WORKSPACE_ACCOUNT names, and is skipped, with the reason, where it
cannot; there, what a command left is ended as the account."""

import asyncio
import base64
import errno
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest

from acme.infra.base import new_id, utcnow
from acme.infra.docker import DOCKER_VARIABLES
from acme.infra.exceptions import InfraNotFound, InfraValidationFailed
from acme.infra.machines.lima import MachinesLimaImpl, hypervisor
from acme.infra.machines.twin import MachinesTwinImpl
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
from acme.infra.transports.processes import DRAIN_SECONDS, GRACE_SECONDS
from acme.infra.transports.redaction import forms, marker
from acme.infra.transports.twin import RecordSealTwin, TransportNullImpl
from acme.infra.transports.vm import TransportVmImpl
from acme.infra.workspaces import (
    EgressMode,
    EgressPolicy,
    IsolationMode,
    IsolationRefused,
    IsolationSpec,
    Workspace,
    WorkspaceProviderInterface,
)
from acme.infra.workspaces.account import WorkspaceAccountImpl, switch_to
from acme.infra.workspaces.container import WorkspaceContainerImpl
from acme.infra.workspaces.host import WorkspaceHostImpl
from acme.infra.workspaces.vm import WorkspaceVmImpl

SECRET = 'tok-3f9A/b+c="q"\\9z-0123456789'
TOKEN = SecretUse(name="api_token", via=SecretVia.INJECTED, env="API_TOKEN")
BROKERED = SecretUse(name="repo_token", via=SecretVia.BROKERED, destination="git.example.test")
ENGINE_CREDENTIAL = "ACME_DATABASE_URL"
SEAL = RecordSealTwin().seal
"""The seal a command goes with, as its session's; a case that revokes one
makes its own."""

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

LEAVES_A_CHILD = 'sleep 60 & echo "$!" > left.pid; (sleep 0.3; echo late) & echo started; exit 7'
"""A command that exits with a child holding its output, and another that
prints once it has exited."""

IO = Path("/proc/self/io")
"""Where this process's count of the bytes it has read is, on Linux."""

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


def kept_at(records: Path) -> bytes:
    """Every record a transport keeps under `records`, as kept."""
    return b"".join(path.read_bytes() for path in sorted(records.rglob("*.json")))


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

    @pytest.fixture
    def records(self, tmp_path: Path) -> Path:
        """Where the transport keeps its records, on this host."""
        return tmp_path / "records"

    async def test_a_command_answers_its_exit_and_its_output(
        self, transport: TransportInterface, workspace: Workspace
    ) -> None:
        result = await transport.run(
            workspace, command("sh", "-c", "echo out; echo err >&2; exit 3"), seal=SEAL
        )
        assert (result.exit_code, result.stdout, result.stderr) == (3, "out\n", "err\n")
        assert not result.timed_out and result.secrets == ()

    async def test_an_output_past_its_bound_keeps_its_head_and_its_tail(
        self, transport: TransportInterface, workspace: Workspace
    ) -> None:
        """A command's end, its summary or the error that stopped it, is kept
        however much it printed before it."""
        script = "echo BEGIN; i=0; while [ $i -lt 2000 ]; do echo 0123456789; i=$((i+1)); done; "
        result = await transport.run(
            workspace, command("sh", "-c", script + "echo THE END", max_output=1000), seal=SEAL
        )
        assert result.exit_code == 0 and result.truncated
        assert result.stdout.startswith("BEGIN\n") and result.stdout.endswith("THE END\n")
        head, _, tail = result.stdout.partition("\n[")
        cut, _, tail = tail.partition(" characters cut here]\n")
        assert len(head) + len(tail) == 1000 and int(cut) == 6 + 2000 * 11 + 8 - 1000

    async def test_a_viewer_that_fails_costs_the_view_never_the_command(
        self, transport: TransportInterface, workspace: Workspace
    ) -> None:
        async def failing(stream: str, text: str) -> None:
            raise RuntimeError("the viewer went away")

        result = await transport.run(
            workspace,
            command("sh", "-c", "for i in 1 2 3; do echo line$i; sleep 0.05; done"),
            failing,
            seal=SEAL,
        )
        assert (result.exit_code, result.stdout) == (0, "line1\nline2\nline3\n")
        assert not result.timed_out

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
            seal=SEAL,
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
            seal=SEAL,
        )
        assert "repo_token" not in result.stdout.lower() and result.secrets == ("repo_token",)
        assert broker.attached == {} and broker.detached == [result.key]

    async def test_the_whole_tree_ends_at_the_deadline(
        self, transport: TransportInterface, workspace: Workspace
    ) -> None:
        started = utcnow()
        result = await transport.run(
            workspace, command(self.python, "-c", SPAWNS_A_TREE, seconds=3), seal=SEAL
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
            found = await transport.run(workspace, command("sh", "-c", check, *pids), seal=SEAL)
            alive = found.stdout.split()
            if not alive:
                return []
            await asyncio.sleep(0.2)
        return alive

    async def test_a_stale_epoch_is_refused_and_runs_nothing(
        self, transport: TransportInterface, workspace: Workspace
    ) -> None:
        await transport.run(workspace, command("true", epoch=5), seal=SEAL)
        stale = command("sh", "-c", "touch ran", epoch=4)
        with pytest.raises(StaleCommand):
            await transport.run(workspace, stale, seal=SEAL)
        with pytest.raises(StaleCommand):
            await transport.write_file(workspace, "x", b"x", epoch=4)
        assert [entry.path for entry in await transport.list_files(workspace, ".", 10)] == []
        assert await transport.outcome(workspace, stale.key, epoch=5, seal=SEAL) is None
        with pytest.raises(StaleCommand):
            await transport.outcome(workspace, stale.key, epoch=4, seal=SEAL)

    async def test_how_a_command_ended_is_recorded_under_its_key(
        self, transport: TransportInterface, workspace: Workspace
    ) -> None:
        sent = command("sh", "-c", "echo done; exit 2")
        result = await transport.run(workspace, sent, seal=SEAL)
        assert await transport.outcome(workspace, sent.key, epoch=1, seal=SEAL) == result
        assert await transport.outcome(workspace, new_id(), epoch=1, seal=SEAL) is None

    async def test_a_records_output_opens_under_its_seal_alone_and_is_noise_once_revoked(
        self, transport: TransportInterface, workspace: Workspace, records: Path
    ) -> None:
        sealing = RecordSealTwin()
        sent = command("sh", "-c", "echo the-build-log-line; exit 4")
        result = await transport.run(workspace, sent, seal=sealing.seal)
        assert result.stdout == "the-build-log-line\n"
        at_rest = kept_at(records)
        assert b'"exit_code":4' in at_rest, "how it ended is readable"
        assert b"the-build-log-line" not in at_rest, "what it printed is sealed"
        assert await transport.outcome(workspace, sent.key, epoch=1, seal=sealing.seal) == result
        with pytest.raises(ValueError, match="does not open"):
            await transport.outcome(workspace, sent.key, epoch=1, seal=RecordSealTwin().seal)
        sealing.revoke()
        erased = await transport.outcome(workspace, sent.key, epoch=1, seal=sealing.seal)
        assert erased == result.model_copy(update={"stdout": "", "stderr": ""})

    async def test_a_workspaces_purged_records_are_gone_from_the_host(
        self, transport: TransportInterface, workspace: Workspace, records: Path
    ) -> None:
        sent = command("echo", "printed")
        await transport.run(workspace, sent, seal=SEAL)
        assert list((records / workspace.id.hex).glob("*.json"))
        await transport.purge_records(workspace.id)
        assert not (records / workspace.id.hex).exists()
        await transport.purge_records(workspace.id)
        assert await transport.outcome(workspace, sent.key, epoch=1, seal=SEAL) is None

    async def test_asking_for_an_outcome_fences_the_run_it_replaces(
        self, transport: TransportInterface, workspace: Workspace
    ) -> None:
        lost = command("sh", "-c", "touch ran", epoch=1)
        assert await transport.outcome(workspace, lost.key, epoch=2, seal=SEAL) is None
        with pytest.raises(StaleCommand):
            await transport.run(workspace, lost, seal=SEAL)
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
        result = await transport.run(workspace, command("cat", "app.py", cwd="src"), seal=SEAL)
        assert result.stdout == "print('hi')\n"
        with pytest.raises(InfraNotFound):
            await transport.read_file(workspace, "missing.txt", 10)
        for outside in ("../x", "/etc/passwd", "src/../../x"):
            with pytest.raises(PathOutsideWorkspace):
                await transport.read_file(workspace, outside, 10)

    async def test_a_read_from_an_offset_answers_only_what_follows_it(
        self, transport: TransportInterface, workspace: Workspace
    ) -> None:
        """A caller that follows a growing file reads only what is new."""
        await transport.write_file(workspace, "run.log", b"first\n", epoch=1)
        assert await transport.read_file(workspace, "run.log", 100) == b"first\n"
        grown = await transport.run(
            workspace, command("sh", "-c", "printf 'second\\n' >> run.log"), seal=SEAL
        )
        assert grown.exit_code == 0, grown.stderr
        assert await transport.read_file(workspace, "run.log", 100, offset=6) == b"second\n"
        assert await transport.read_file(workspace, "run.log", 3, offset=8) == b"con"
        for past in (13, 14):
            assert await transport.read_file(workspace, "run.log", 100, offset=past) == b""
        with pytest.raises(InfraValidationFailed):
            await transport.read_file(workspace, "run.log", 100, offset=-1)

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
                await transport.run(refused, command("true"), seal=SEAL)
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


class LocalContract(TransportContract):
    """What the local transport holds, as this process and as an account."""

    async def test_a_command_is_over_when_its_own_process_exits(
        self, transport: TransportInterface, workspace: Workspace
    ) -> None:
        await self._left_ends(transport, workspace)

    async def _left_ends(self, transport: TransportInterface, workspace: Workspace) -> None:
        """A process the command left holding its output never holds it to
        its deadline: what is printed while the output drains is kept, and
        the process is ended at the drain's bound."""
        started = utcnow()
        result = await transport.run(
            workspace, command("sh", "-c", LEAVES_A_CHILD, seconds=60), seal=SEAL
        )
        took = utcnow() - started
        assert (result.exit_code, result.timed_out) == (7, False), result.stderr
        assert result.stdout == "started\nlate\n"
        assert took < timedelta(seconds=DRAIN_SECONDS + GRACE_SECONDS + 2), took
        left = (await transport.read_file(workspace, "left.pid", 100)).decode().split()
        assert len(left) == 1, left
        alive = await self._alive(transport, workspace, left)
        assert alive == [], f"left running: {alive}"

    async def test_a_read_follows_no_link_in_the_workspace(
        self, transport: TransportInterface, workspace: Workspace
    ) -> None:
        """A caller that polls a path reads the file there, never one a link
        swapped in, wherever the link points."""
        await transport.write_file(workspace, "run.log", b"0123456789", epoch=1)
        planted = "ln -s run.log follow.log; mkdir real; cp run.log real/; ln -s real into"
        result = await transport.run(workspace, command("sh", "-c", planted), seal=SEAL)
        assert result.exit_code == 0, result.stderr
        for linked in ("follow.log", "into/run.log"):
            for offset in (0, 4):
                with pytest.raises(PathOutsideWorkspace):
                    await transport.read_file(workspace, linked, 100, offset=offset)
        assert await transport.read_file(workspace, "real/run.log", 3, offset=4) == b"456"

    @pytest.mark.skipif(not IO.exists(), reason="needs /proc/self/io, which counts what is read")
    async def test_a_read_from_an_offset_reads_no_byte_before_it(
        self, transport: TransportInterface, workspace: Workspace
    ) -> None:
        """A stream's poll costs what is new, never the file again: this
        process reads far fewer bytes than the offset skips."""
        skipped = 8 * 1024 * 1024
        await transport.write_file(workspace, "big.log", b"0" * skipped + b"the tail", epoch=1)
        before = read_so_far()
        assert await transport.read_file(workspace, "big.log", 100, offset=skipped) == b"the tail"
        read = read_so_far() - before
        assert read < skipped // 8, f"{read} bytes read to answer 8"


def read_so_far() -> int:
    """The bytes this process has read, by every thread, as Linux counts
    them (`rchar`)."""
    for line in IO.read_text().splitlines():
        key, _, value = line.partition(":")
        if key == "rchar":
            return int(value)
    raise AssertionError(f"no rchar in {IO}")


class TestTransportLocal(LocalContract):
    @pytest.fixture
    async def workspace(self, tmp_path: Path) -> Workspace:
        provider = WorkspaceHostImpl(tmp_path / "workspaces")
        spec = IsolationSpec(mode=IsolationMode.HOST, egress=EgressPolicy(mode=EgressMode.OPEN))
        return await provider.prepare(new_id(), new_id(), spec)

    @pytest.fixture
    def transport(
        self, records: Path, workspace: Workspace, broker: BrokerTwinImpl
    ) -> TransportInterface:
        python = Path(sys.executable).parent
        return TransportLocalImpl(
            records,
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
            await transport.run(workspace, sent, seal=SEAL)
        assert not (Path(workspace.location) / "ran").exists()


ACCOUNT = os.environ.get("TEST_WORKSPACE_ACCOUNT", "")


def cannot_switch() -> str | None:
    """Why this host cannot run a command as the account under test; None
    when it can."""
    if not ACCOUNT:
        return "TEST_WORKSPACE_ACCOUNT names no account this process may switch to"
    try:
        switch_to(ACCOUNT)
    except IsolationRefused as refused:
        return refused.message
    return None


@pytest.mark.skipif(cannot_switch() is not None, reason=f"{cannot_switch()}")
class TestTransportAccount(LocalContract):
    """Every command runs as the account, and a path follows no link."""

    @pytest.fixture
    def root(self) -> Iterator[Path]:
        """A root the account passes through, unlike the test's own
        temporary directory, which only this process enters."""
        root = Path(tempfile.mkdtemp(prefix="workspaces-"))
        root.chmod(0o711)
        yield root
        shutil.rmtree(root, ignore_errors=True)

    @pytest.fixture
    async def workspace(self, root: Path) -> AsyncIterator[Workspace]:
        """Under a unit that hides the host's linking setting, the test
        declares it on, as a runner's settings do; where it shows, it
        decides."""
        provider = WorkspaceAccountImpl(root, ACCOUNT, protected_hardlinks=True)
        spec = IsolationSpec(mode=IsolationMode.ACCOUNT, egress=EgressPolicy(mode=EgressMode.OPEN))
        workspace = await provider.prepare(new_id(), new_id(), spec)
        yield workspace
        await provider.release(workspace)
        await provider.purge(workspace.org_id, workspace.id)

    @pytest.fixture
    def transport(
        self, records: Path, workspace: Workspace, broker: BrokerTwinImpl
    ) -> TransportInterface:
        python = Path(sys.executable).parent
        return TransportLocalImpl(
            records,
            secrets_for(workspace.org_id),
            broker,
            search_path=f"{python}:{DEFAULT_PATH}",
            account=ACCOUNT,
        )

    async def test_a_link_the_account_plants_is_never_followed(
        self, transport: TransportInterface, workspace: Workspace, tmp_path: Path
    ) -> None:
        """This process reads and writes with more than the account may, so
        a link there, wherever it points, is refused."""
        outside = tmp_path / "outside.txt"
        outside.write_text("the engine's")
        planted = f"ln -s {outside} link; mkdir real; ln -s real into"
        result = await transport.run(workspace, command("sh", "-c", planted), seal=SEAL)
        assert result.exit_code == 0, result.stderr
        with pytest.raises(PathOutsideWorkspace):
            await transport.read_file(workspace, "link", 100)
        with pytest.raises(PathOutsideWorkspace):
            await transport.write_file(workspace, "link", b"the model's", epoch=1)
        with pytest.raises(PathOutsideWorkspace):
            await transport.write_file(workspace, "into/x", b"the model's", epoch=1)
        with pytest.raises(PathOutsideWorkspace):
            await transport.list_files(workspace, "into", 10)
        assert outside.read_text() == "the engine's"

    async def test_what_a_command_left_is_ended_as_the_account(
        self, transport: TransportInterface, workspace: Workspace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Where this process may not signal the account's processes, what a
        command left holding its output still ends: the account ends it."""

        def refused(*_: object) -> None:
            raise PermissionError(errno.EPERM, "not this process's to signal")

        monkeypatch.setattr(os, "kill", refused)
        monkeypatch.setattr(os, "killpg", refused)
        await self._left_ends(transport, workspace)

    async def test_a_file_of_this_process_with_a_second_link_is_never_read_or_written(
        self, transport: TransportInterface, workspace: Workspace, root: Path
    ) -> None:
        """A hard link in the workspace to a file of this process outside it,
        as the account makes on a host that lets it link a file it does not
        own, is refused, and the file outside stays as it was."""
        outside = root / "outside.txt"
        outside.write_text("the engine's")
        os.link(outside, Path(workspace.location) / "hard")
        with pytest.raises(PathOutsideWorkspace):
            await transport.read_file(workspace, "hard", 100)
        with pytest.raises(PathOutsideWorkspace):
            await transport.write_file(workspace, "hard", b"the model's", epoch=1)
        assert outside.read_text() == "the engine's"


async def test_the_null_transport_refuses_every_call() -> None:
    null = TransportNullImpl()
    workspace = Workspace.absent(new_id(), new_id())
    with pytest.raises(CapabilityMissing):
        await null.run(workspace, command("true"), seal=SEAL)
    with pytest.raises(CapabilityMissing):
        await null.outcome(workspace, new_id(), epoch=1, seal=SEAL)
    with pytest.raises(CapabilityMissing):
        await null.write_file(workspace, "x", b"", epoch=1)
    with pytest.raises(CapabilityMissing):
        await null.list_files(workspace, ".", 1)
    await null.purge_records(workspace.id)  # it keeps no record, so none is left


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
        await provider.purge(workspace.org_id, workspace.id)

    @pytest.fixture
    def workspace(self, provided: tuple[WorkspaceProviderInterface, Workspace]) -> Workspace:
        return provided[1]

    @pytest.fixture
    def transport(
        self, records: Path, workspace: Workspace, broker: BrokerTwinImpl
    ) -> TransportInterface:
        return TransportContainerImpl(
            records, secrets_for(workspace.org_id), broker, timedelta(seconds=60)
        )

    async def test_no_egress_reaches_nothing(
        self, transport: TransportInterface, workspace: Workspace
    ) -> None:
        reach = "import socket; socket.create_connection(('1.1.1.1', 53), timeout=3)"
        result = await transport.run(workspace, command(self.python, "-c", reach), seal=SEAL)
        assert result.exit_code != 0

    async def test_a_commands_own_variables_stay_inside_the_container(
        self, transport: TransportInterface, workspace: Workspace
    ) -> None:
        """A tool's `PATH` and `HOME` are the command's, never the `docker`
        command line's: it still finds Docker, and the same Docker."""
        env = (("PATH", "/usr/bin:/bin"), ("HOME", "/workspace/home"))
        script = "echo $HOME; echo $PATH"
        result = await transport.run(workspace, command("sh", "-c", script, env=env), seal=SEAL)
        assert result.exit_code == 0, result.stderr
        assert result.stdout.splitlines() == ["/workspace/home", "/usr/bin:/bin"]

    async def test_a_purged_workspace_takes_its_files_with_it(
        self,
        transport: TransportInterface,
        provided: tuple[WorkspaceProviderInterface, Workspace],
    ) -> None:
        provider, workspace = provided
        await transport.write_file(workspace, "kept.txt", b"kept", epoch=1)
        await provider.purge(workspace.org_id, workspace.id)
        again = await provider.prepare(workspace.org_id, workspace.id, workspace.spec)
        with pytest.raises(InfraNotFound):
            await transport.read_file(again, "kept.txt", 10)

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


VM = IsolationSpec(mode=IsolationMode.VM, egress=EgressPolicy(mode=EgressMode.OPEN))


class TestTransportVmTwin(TransportContract):
    """The VM transport over a machine of the machines' twin: a folder of this
    host, its commands processes of this host run from it."""

    @pytest.fixture
    async def provided(self, tmp_path: Path) -> AsyncIterator[tuple[MachinesTwinImpl, Workspace]]:
        machines = MachinesTwinImpl(tmp_path / "machines")
        provider = WorkspaceVmImpl(machines, "twin", "acme-test-", timedelta(seconds=60))
        workspace = await provider.prepare(new_id(), new_id(), VM)
        yield machines, workspace
        await provider.purge(workspace.org_id, workspace.id)

    @pytest.fixture
    def workspace(self, provided: tuple[MachinesTwinImpl, Workspace]) -> Workspace:
        return provided[1]

    @pytest.fixture
    def transport(
        self, records: Path, provided: tuple[MachinesTwinImpl, Workspace], broker: BrokerTwinImpl
    ) -> TransportInterface:
        machines, workspace = provided
        timeout = timedelta(seconds=60)
        return TransportVmImpl(records, secrets_for(workspace.org_id), broker, machines, timeout)


def lima_runs() -> bool:
    return shutil.which("limactl") is not None and hypervisor() is None


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.skipif(not lima_runs(), reason="needs Lima and a hypervisor")
class TestTransportVmLima(TransportContract):
    """The VM transport in one machine on Lima, made for the class, named
    under `ACME_MACHINE_PREFIX`, and destroyed at its end."""

    @pytest.fixture(scope="class")
    def provided(self) -> Iterator[tuple[MachinesLimaImpl, Workspace]]:
        # A machine boots for the class, not for each case; its calls run
        # in a loop of their own, as each is a process of its own.
        machines = MachinesLimaImpl(timedelta(seconds=120), timedelta(minutes=15), encrypted=True)
        prefix = os.environ.get("ACME_MACHINE_PREFIX", "acme-test-")
        provider = WorkspaceVmImpl(
            machines, "template:_images/ubuntu-lts", prefix, timedelta(seconds=120)
        )
        workspace = asyncio.run(provider.prepare(new_id(), new_id(), VM))
        yield machines, workspace
        asyncio.run(provider.purge(workspace.org_id, workspace.id))

    @pytest.fixture
    def workspace(self, provided: tuple[MachinesLimaImpl, Workspace]) -> Workspace:
        return provided[1]

    @pytest.fixture
    def transport(
        self, records: Path, provided: tuple[MachinesLimaImpl, Workspace], broker: BrokerTwinImpl
    ) -> TransportInterface:
        machines, workspace = provided
        timeout = timedelta(seconds=120)
        return TransportVmImpl(records, secrets_for(workspace.org_id), broker, machines, timeout)


def test_a_secret_in_the_base64_of_a_basic_header_is_one_of_the_forms() -> None:
    """The fixture's script prints this one; the redactor knows it."""
    encoded = base64.b64encode(b"Authorization: Basic " + SECRET.encode()).decode()
    assert any(form in encoded for form in forms(SECRET))


async def test_the_docker_command_line_keeps_its_own_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plain variables go by name and value, a secret by name alone, and the
    command line's environment is its own and the secrets', never the
    command's variables."""
    seen: dict[str, object] = {}

    async def spawned(argv: list[str], cwd: Path, env: dict[str, str]) -> object:
        seen.update(argv=argv, env=env)
        raise RuntimeError("seen")

    monkeypatch.setattr("acme.infra.transports.container.spawn", spawned)
    for name in DOCKER_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PATH", "/engine/bin:/usr/bin")
    monkeypatch.setenv("HOME", "/engine/home")
    workspace = Workspace(
        id=new_id(),
        org_id=new_id(),
        spec=IsolationSpec(mode=IsolationMode.CONTAINER, egress=EgressPolicy(mode=EgressMode.NONE)),
        location="acme-ws-test",
    )
    transport = TransportContainerImpl(
        tmp_path, secrets_for(workspace.org_id), BrokerTwinImpl(), timedelta(seconds=5)
    )
    env = (("PATH", "/tool/bin"), ("HOME", "/workspace/home"))
    with pytest.raises(RuntimeError, match="seen"):
        await transport.run(workspace, command("true", env=env, secrets=(TOKEN,)), seal=SEAL)
    argv = seen["argv"]
    assert isinstance(argv, list)
    flags = [argv[i + 1] for i, flag in enumerate(argv) if flag == "--env"]
    assert "PATH=/tool/bin" in flags and "HOME=/workspace/home" in flags
    assert "API_TOKEN" in flags and not any(SECRET in flag for flag in argv)
    assert seen["env"] == {
        "PATH": "/engine/bin:/usr/bin",
        "HOME": "/engine/home",
        "API_TOKEN": SECRET,
    }
