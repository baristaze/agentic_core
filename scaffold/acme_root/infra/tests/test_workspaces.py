"""Isolation is chosen up front and never weakened. A provider meets a spec
whole or refuses it, before it creates anything, and never hands back a
weaker place: not the host directory for a container, and not a container
with open egress for one that asked for none. A host directory's release
ends what its commands left running there.

An account workspace runs its commands as an account of the host, which the
cases that need one name in TEST_WORKSPACE_ACCOUNT. They are skipped, with
the reason, on a host that cannot switch to it: one not on Linux, or a
process without the capabilities the switch takes, as in most CI."""

import asyncio
import contextlib
import os
import re
import shutil
import signal
import tempfile
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest

from acme.infra.base import new_id, utcnow
from acme.infra.docker import DockerReply
from acme.infra.exceptions import BackendFailed
from acme.infra.impl.configured import InfraConfiguredImpl
from acme.infra.impl.settings import InfraSettings
from acme.infra.secrets.local import SecretsLocalImpl
from acme.infra.transports import CommandResult, CommandSpec
from acme.infra.transports.broker import BrokerNullImpl
from acme.infra.transports.local import TransportLocalImpl
from acme.infra.transports.twin import RecordSealTwin
from acme.infra.workspaces import (
    EgressMode,
    EgressPolicy,
    IsolationMode,
    IsolationRefused,
    IsolationSpec,
    ResourceLimits,
    Workspace,
    WorkspaceProviderInterface,
)
from acme.infra.workspaces import account as accounts
from acme.infra.workspaces.account import WorkspaceAccountImpl, switch_to
from acme.infra.workspaces.container import (
    ICC,
    OPEN_NETWORK,
    WorkspaceContainerImpl,
    container_name,
)
from acme.infra.workspaces.host import WorkspaceHostImpl
from acme.infra.workspaces.twin import WorkspaceNullImpl, WorkspaceTwinImpl

NONE = EgressPolicy(mode=EgressMode.NONE)
OPEN = EgressPolicy(mode=EgressMode.OPEN)
ALLOWLIST = EgressPolicy(mode=EgressMode.ALLOWLIST, hosts=("pypi.org",))


def spec(mode: IsolationMode, egress: EgressPolicy = OPEN, **limits: float) -> IsolationSpec:
    return IsolationSpec(mode=mode, egress=egress, limits=ResourceLimits.model_validate(limits))


HOST_REFUSES = [
    spec(IsolationMode.VM),
    spec(IsolationMode.CONTAINER),
    spec(IsolationMode.ACCOUNT),
    spec(IsolationMode.TWIN),
    spec(IsolationMode.HOST, NONE),
    spec(IsolationMode.HOST, ALLOWLIST),
    spec(IsolationMode.HOST, memory_mb=512),
    spec(IsolationMode.HOST, processes=64),
    spec(IsolationMode.HOST, cpus=1),
]

CONTAINER_REFUSES = [
    spec(IsolationMode.VM, NONE),
    spec(IsolationMode.HOST),
    spec(IsolationMode.TWIN, NONE),
    spec(IsolationMode.CONTAINER, ALLOWLIST),
]


@pytest.mark.parametrize("asked", HOST_REFUSES, ids=lambda s: s.model_dump_json())
async def test_a_host_directory_refuses_what_it_cannot_hold_and_makes_nothing(
    tmp_path: Path, asked: IsolationSpec
) -> None:
    root = tmp_path / "workspaces"
    with pytest.raises(IsolationRefused):
        await WorkspaceHostImpl(root).prepare(new_id(), new_id(), asked)
    assert not root.exists(), "nothing is made for a spec that is refused"


async def test_a_host_directory_meets_the_host_mode_and_keeps_its_files_past_a_release(
    tmp_path: Path,
) -> None:
    provider = WorkspaceHostImpl(tmp_path)
    org, workspace_id = new_id(), new_id()
    workspace = await provider.prepare(org, workspace_id, spec(IsolationMode.HOST))
    (Path(workspace.location) / "notes.txt").write_text("kept")
    await provider.release(workspace)
    again = await provider.prepare(org, workspace_id, spec(IsolationMode.HOST))
    assert (Path(again.location) / "notes.txt").read_text() == "kept"
    await provider.purge(org, workspace_id)
    assert not await asyncio.to_thread(Path(again.location).exists)


async def left_behind(cwd: Path) -> int:
    """A process a command left running in `cwd` as one does: put in the
    background with its output sent elsewhere, by a shell that then ended,
    so nothing waits on it. Its id."""
    shell = await asyncio.create_subprocess_exec(
        "sh",
        "-c",
        "sleep 300 >/dev/null 2>&1 & echo $!",
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    out, _ = await shell.communicate()
    return int(out)


async def running(pid: int) -> bool:
    """Whether `pid` still runs, given a few seconds to be gone."""
    for _ in range(30):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        await asyncio.sleep(0.1)
    return True


def kill_quietly(*pids: int) -> None:
    for pid in pids:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)


async def test_a_host_release_ends_what_its_commands_left_running_and_nothing_else(
    tmp_path: Path,
) -> None:
    """Its instance is what runs in it: a release ends each process whose
    directory is inside the workspace, one of this process's own children
    among them, and leaves one elsewhere alone."""
    provider = WorkspaceHostImpl(tmp_path / "workspaces")
    workspace = await provider.prepare(new_id(), new_id(), spec(IsolationMode.HOST))
    nested = Path(workspace.location) / "server"
    nested.mkdir()
    left, deeper = await left_behind(Path(workspace.location)), await left_behind(nested)
    elsewhere = await left_behind(tmp_path)
    child = await asyncio.create_subprocess_exec(
        "sleep", "300", cwd=workspace.location, start_new_session=True
    )
    try:
        await provider.release(workspace)
        assert not await running(left) and not await running(deeper)
        assert await asyncio.wait_for(child.wait(), 10) < 0, "ended by a signal"
        os.kill(elsewhere, 0)  # still there
    finally:
        kill_quietly(left, deeper, elsewhere)
        if child.returncode is None:
            child.kill()
            await child.wait()


def left_locked(location: Path, outside: Path) -> None:
    """What commands leave in a workspace: a module cache read-only, as `go
    mod download` leaves its own, a directory no one may read, and a link
    to a directory outside the workspace."""
    cache = location / "go" / "pkg" / "mod" / "m@v1"
    cache.mkdir(parents=True)
    (cache / "go.mod").write_text("module m\n")
    cache.chmod(0o555)
    cache.parent.chmod(0o555)
    hidden = location / "hidden"
    hidden.mkdir()
    (hidden / "secret.txt").write_text("x")
    hidden.chmod(0)
    outside.mkdir()
    outside.chmod(0o555)
    (location / "elsewhere").symlink_to(outside)


async def test_a_host_purge_removes_what_a_command_left_locked_and_follows_no_link(
    tmp_path: Path,
) -> None:
    provider = WorkspaceHostImpl(tmp_path / "workspaces")
    workspace = await provider.prepare(new_id(), new_id(), spec(IsolationMode.HOST))
    outside = tmp_path / "outside"
    await asyncio.to_thread(left_locked, Path(workspace.location), outside)
    try:
        await provider.purge(workspace.org_id, workspace.id)
        assert not await asyncio.to_thread(Path(workspace.location).exists)
        assert (await asyncio.to_thread(outside.stat)).st_mode & 0o777 == 0o555, "not followed"
    finally:
        await asyncio.to_thread(outside.chmod, 0o755)


async def test_a_host_purge_ends_what_runs_there_before_its_files_go(tmp_path: Path) -> None:
    provider = WorkspaceHostImpl(tmp_path / "workspaces")
    workspace = await provider.prepare(new_id(), new_id(), spec(IsolationMode.HOST))
    left = await left_behind(Path(workspace.location))
    try:
        await provider.purge(workspace.org_id, workspace.id)
        assert not await running(left)
        assert not await asyncio.to_thread(Path(workspace.location).exists)
    finally:
        kill_quietly(left)


@pytest.mark.parametrize("asked", CONTAINER_REFUSES, ids=lambda s: s.model_dump_json())
async def test_a_container_provider_refuses_what_it_cannot_hold_before_it_reaches_docker(
    monkeypatch: pytest.MonkeyPatch, asked: IsolationSpec
) -> None:
    calls: list[tuple[str, ...]] = []

    async def docker(*args: str, **_: object) -> object:
        calls.append(args)
        raise AssertionError("a refused spec reaches no docker")

    monkeypatch.setattr("acme.infra.workspaces.container.docker", docker)
    provider = WorkspaceContainerImpl("python:3.14-slim", timedelta(seconds=5))
    with pytest.raises(IsolationRefused):
        await provider.prepare(new_id(), new_id(), asked)
    assert calls == []


class LocalDocker:
    """Docker, faked for one container: `run` starts it with the labels it
    names, `rm` removes it, and `inspect` renders its format over it as
    Docker does. A network stands once made, with the options it was made
    with. It keeps every command it is given."""

    def __init__(self) -> None:
        self.labels: dict[str, str] | None = None  # the running container's, when one runs
        self.networks: dict[str, dict[str, str]] = {}  # each network's options, by name
        self.calls: list[tuple[str, ...]] = []

    async def __call__(self, *args: str, **_: object) -> DockerReply:
        self.calls.append(args)
        if args[:2] == ("network", "create"):
            pairs = [args[i + 1] for i, arg in enumerate(args) if arg == "--opt"]
            self.networks[args[-1]] = dict(pair.split("=", 1) for pair in pairs)
        elif args[:2] == ("network", "inspect"):
            options = self.networks.get(args[-1])
            if options is None:
                return DockerReply(1, b"", b"Error: No such network")
            wanted = re.search(r'"([^"]+)"', args[args.index("--format") + 1])
            assert wanted is not None
            return DockerReply(0, f"{options.get(wanted.group(1), '<no value>')}\n".encode(), b"")
        elif args[0] == "run":
            pairs = [args[i + 1] for i, arg in enumerate(args) if arg == "--label"]
            self.labels = dict(pair.split("=", 1) for pair in pairs)
        elif args[0] == "rm":
            self.labels = None
        elif args[0] == "inspect":
            if self.labels is None:
                return DockerReply(1, b"", b"Error: No such object")
            labels = self.labels
            rendered = re.sub(
                r'\{\{index \.Config\.Labels "([^"]+)"\}\}',
                lambda found: labels.get(found.group(1), "<no value>"),
                args[args.index("--format") + 1].replace("{{.State.Running}}", "true"),
            )
            return DockerReply(0, f"{rendered}\n".encode(), b"")
        return DockerReply(0, b"", b"")


async def test_a_running_container_is_reused_only_under_the_spec_it_was_started_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A session whose spec was tightened since its container started, from
    open egress to none, never gets that container back: it is replaced,
    and its files, in their volume, are kept."""
    opened, closed = spec(IsolationMode.CONTAINER, OPEN), spec(IsolationMode.CONTAINER, NONE)
    docker = LocalDocker()
    monkeypatch.setattr("acme.infra.workspaces.container.docker", docker)
    provider = WorkspaceContainerImpl("python:3.14-slim", timedelta(seconds=5))
    org, workspace_id = new_id(), new_id()
    await provider.prepare(org, workspace_id, opened)
    docker.calls.clear()
    await provider.prepare(org, workspace_id, opened)
    assert [call[0] for call in docker.calls] == ["version", "inspect"], "the same spec reuses it"
    docker.calls.clear()
    await provider.prepare(org, workspace_id, closed)
    assert [call[0] for call in docker.calls] == ["version", "inspect", "rm", "volume", "run"]
    run = docker.calls[-1]
    assert run[run.index("--network") + 1] == "none", "the new container holds the tighter spec"
    assert not any(call[:2] == ("volume", "rm") for call in docker.calls), "its files stay"


async def test_open_egress_workspaces_share_a_bridge_where_none_reaches_another(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two workspaces with open egress never land on Docker's default
    bridge, where every container reaches every other: each joins the one
    bridge made with traffic between its containers off, made once."""
    docker = LocalDocker()
    monkeypatch.setattr("acme.infra.workspaces.container.docker", docker)
    provider = WorkspaceContainerImpl("python:3.14-slim", timedelta(seconds=5))
    opened = spec(IsolationMode.CONTAINER, OPEN)

    await provider.prepare(new_id(), new_id(), opened)
    docker.labels = None  # the double holds one container: the second is another
    await provider.prepare(new_id(), new_id(), opened)

    runs = [call for call in docker.calls if call[0] == "run"]
    assert [run[run.index("--network") + 1] for run in runs] == [OPEN_NETWORK, OPEN_NETWORK]
    made = [call for call in docker.calls if call[:2] == ("network", "create")]
    assert len(made) == 1 and docker.networks == {OPEN_NETWORK: {ICC: "false"}}


async def test_a_bridge_that_lets_its_containers_reach_each_other_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A network of that name made by anything else, with traffic between
    its containers on, is never joined: the workspace is refused and no
    container starts."""
    docker = LocalDocker()
    docker.networks[OPEN_NETWORK] = {ICC: "true"}
    monkeypatch.setattr("acme.infra.workspaces.container.docker", docker)
    provider = WorkspaceContainerImpl("python:3.14-slim", timedelta(seconds=5))

    with pytest.raises(IsolationRefused, match="reach each other"):
        await provider.prepare(new_id(), new_id(), spec(IsolationMode.CONTAINER, OPEN))
    assert not any(call[0] == "run" for call in docker.calls)


async def test_a_container_purge_by_ids_removes_its_container_and_its_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A purge needs no workspace in hand: the container and its volume are
    named by the workspace's id. A Docker that cannot remove them fails the
    purge, so nothing is left behind as if it went."""
    docker = LocalDocker()
    monkeypatch.setattr("acme.infra.workspaces.container.docker", docker)
    provider = WorkspaceContainerImpl("python:3.14-slim", timedelta(seconds=5))
    workspace_id = new_id()
    name = container_name(workspace_id)
    await provider.purge(new_id(), workspace_id)
    assert docker.calls == [("rm", "-f", name), ("volume", "rm", "-f", name)]

    async def unreachable(*args: str, **_: object) -> DockerReply:
        return DockerReply(1, b"", b"Cannot connect to the Docker daemon")

    monkeypatch.setattr("acme.infra.workspaces.container.docker", unreachable)
    with pytest.raises(BackendFailed):
        await provider.purge(new_id(), workspace_id)


async def test_a_container_spec_with_no_docker_is_refused_and_never_swapped_for_a_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The configured root picks the container backend; with no Docker to
    reach, the session's spec is refused, and no host directory is made in
    its place."""
    monkeypatch.setenv("DOCKER_HOST", f"unix://{tmp_path}/no-docker.sock")
    root = tmp_path / "workspaces"
    infra = InfraConfiguredImpl(
        InfraSettings.model_validate(
            {
                "environment": "local",
                "secrets_file": tmp_path / "secrets.env",
                "workspace_backend": "container",
                "workspaces_root": root,
                "docker_timeout_seconds": 20,
            }
        )
    )
    with pytest.raises(IsolationRefused, match="needs Docker"):
        await infra.get_workspaces().prepare(
            new_id(), new_id(), spec(IsolationMode.CONTAINER, NONE)
        )
    assert not root.exists()


@pytest.mark.parametrize(
    "provider", [WorkspaceTwinImpl(), WorkspaceNullImpl()], ids=lambda p: p.describe()
)
async def test_the_twin_and_the_null_provider_refuse_every_mode_not_theirs(
    provider: WorkspaceProviderInterface,
) -> None:
    for mode in (
        IsolationMode.VM,
        IsolationMode.CONTAINER,
        IsolationMode.HOST,
        IsolationMode.ACCOUNT,
    ):
        with pytest.raises(IsolationRefused):
            await provider.prepare(new_id(), new_id(), spec(mode, NONE))
    if isinstance(provider, WorkspaceNullImpl):
        with pytest.raises(IsolationRefused):
            await provider.prepare(new_id(), new_id(), spec(IsolationMode.TWIN, NONE))
    else:
        twin = await provider.prepare(new_id(), new_id(), spec(IsolationMode.TWIN, NONE, cpus=1))
        assert twin.spec.limits.cpus == 1


def test_an_allowlist_names_its_hosts_and_no_other_egress_does() -> None:
    with pytest.raises(ValueError):
        EgressPolicy(mode=EgressMode.ALLOWLIST)
    with pytest.raises(ValueError):
        EgressPolicy(mode=EgressMode.OPEN, hosts=("pypi.org",))


def test_the_absent_workspace_is_the_none_mode() -> None:
    absent = Workspace.absent(new_id(), new_id())
    assert absent.spec.mode is IsolationMode.NONE and absent.location == ""


ACCOUNT = os.environ.get("TEST_WORKSPACE_ACCOUNT", "")

ACCOUNT_REFUSES = [
    spec(IsolationMode.HOST),
    spec(IsolationMode.CONTAINER),
    spec(IsolationMode.ACCOUNT, NONE),
    spec(IsolationMode.ACCOUNT, ALLOWLIST),
    spec(IsolationMode.ACCOUNT, memory_mb=512),
    spec(IsolationMode.ACCOUNT, cpus=1),
    spec(IsolationMode.ACCOUNT, processes=64, memory_mb=512),
]


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


needs_an_account = pytest.mark.skipif(cannot_switch() is not None, reason=f"{cannot_switch()}")


def proc_hides_others() -> bool:
    """Whether `/proc` hides another account's processes from this one, as a
    unit with `ProtectProc=invisible` sees it: mounted with `hidepid`, and
    this process outside the group it exempts, root's unless `gid=` names
    another."""
    try:
        mounts = Path("/proc/self/mounts").read_text()
    except OSError:
        return False
    options: dict[str, str] = {}
    for fields in (line.split() for line in mounts.splitlines()):
        if len(fields) > 3 and fields[1] == "/proc":
            pairs = (option.partition("=") for option in fields[3].split(","))
            options = {key: value for key, _, value in pairs}
    exempt = int(options.get("gid", "0"))
    return options.get("hidepid", "0") not in {"0", "off"} and exempt not in {
        os.getgid(),
        *os.getgroups(),
    }


@pytest.fixture
def account_root() -> Iterator[Path]:
    """A root the account passes through, unlike the test's own temporary
    directory, which only this process enters."""
    root = Path(tempfile.mkdtemp(prefix="workspaces-"))
    root.chmod(0o711)
    yield root
    shutil.rmtree(root, ignore_errors=True)


def as_account(records: Path) -> TransportLocalImpl:
    return TransportLocalImpl(
        records, SecretsLocalImpl(None, {}), BrokerNullImpl(), account=ACCOUNT
    )


async def ran(
    transport: TransportLocalImpl, workspace: Workspace, script: str, seconds: float = 30
) -> CommandResult:
    sent = CommandSpec(
        argv=("sh", "-c", script),
        key=new_id(),
        epoch=1,
        deadline=utcnow() + timedelta(seconds=seconds),
    )
    return await transport.run(workspace, sent, seal=RecordSealTwin().seal)


def alive(pid: int) -> bool:
    """Whether `pid` runs, as the kernel answers a signal of none, which no
    `/proc` hides; a zombie, where `/proc` shows one, is dead, whoever reaps
    it."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    try:
        status = Path(f"/proc/{pid}/status").read_text()
    except OSError:
        return True
    return not re.search(r"^State:\s+Z", status, re.MULTILINE)


async def still_alive(*pids: int) -> list[int]:
    """The pids still running, given a few seconds to be gone."""
    left = list(pids)
    for _ in range(30):
        if not (left := [pid for pid in pids if alive(pid)]):
            return []
        await asyncio.sleep(0.1)
    return left


@pytest.mark.parametrize("asked", ACCOUNT_REFUSES, ids=lambda s: s.model_dump_json())
async def test_an_account_workspace_refuses_what_it_cannot_hold_and_makes_nothing(
    tmp_path: Path, asked: IsolationSpec
) -> None:
    """Egress, a share of the cpus, and a bound on memory are each refused
    before anything runs: the mode never holds a weaker form of them."""
    root = tmp_path / "workspaces"
    provider = WorkspaceAccountImpl(root, ACCOUNT or "acme-agent")
    with pytest.raises(IsolationRefused):
        await provider.prepare(new_id(), new_id(), asked)
    assert not root.exists(), "nothing is made for a spec that is refused"


async def test_an_account_workspace_on_a_host_that_cannot_switch_is_refused(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspaces"
    provider = WorkspaceAccountImpl(root, "no-such-account-here")
    with pytest.raises(IsolationRefused):
        await provider.prepare(new_id(), new_id(), spec(IsolationMode.ACCOUNT, processes=64))
    assert not root.exists()


@needs_an_account
async def test_an_account_workspace_without_the_switchs_capabilities_is_refused(
    monkeypatch: pytest.MonkeyPatch, account_root: Path
) -> None:
    monkeypatch.setattr(accounts, "effective_capabilities", lambda: {5})
    with pytest.raises(IsolationRefused, match="CAP_SETGID, CAP_SETUID, CAP_SETPCAP"):
        await WorkspaceAccountImpl(account_root, ACCOUNT).prepare(
            new_id(), new_id(), spec(IsolationMode.ACCOUNT)
        )
    assert await asyncio.to_thread(os.listdir, account_root) == []


@needs_an_account
async def test_a_command_runs_as_the_account_with_no_group_no_capability_and_no_way_to_gain_one(
    monkeypatch: pytest.MonkeyPatch, account_root: Path, tmp_path: Path
) -> None:
    """Its uid and gid, no supplementary group, every capability set empty,
    `no_new_privs`, the processes it asked for at most, and an environment
    of its own, its home and temporary directory in the workspace's."""
    monkeypatch.setenv("ENGINE_OWN", "this process's alone")
    provider = WorkspaceAccountImpl(account_root, ACCOUNT)
    workspace = await provider.prepare(
        new_id(), new_id(), spec(IsolationMode.ACCOUNT, processes=64)
    )
    try:
        script = (
            "id -u; id -g; id -G; sed -n 's/^Max processes *\\([0-9]*\\).*/\\1/p' /proc/self/limits; "
            "echo $HOME; echo $TMPDIR; "
            "grep -E '^(CapInh|CapPrm|CapEff|CapAmb|NoNewPrivs):' /proc/self/status; "
            "env | cut -d= -f1 | sort | tr '\\n' ' '"
        )
        result = await ran(as_account(tmp_path / "records"), workspace, script)
        assert result.exit_code == 0, result.stderr
        uid, gid, groups, processes, home, tmp, *held, names = result.stdout.splitlines()
        account = switch_to(ACCOUNT)
        assert (int(uid), int(gid), groups.split()) == (
            account.uid,
            account.gid,
            [str(account.gid)],
        )
        assert processes == "64"
        assert home == workspace.location and tmp == str(Path(workspace.location).parent / "tmp")
        assert dict(line.split(":\t") for line in held) == {
            "CapInh": "0000000000000000",
            "CapPrm": "0000000000000000",
            "CapEff": "0000000000000000",
            "CapAmb": "0000000000000000",
            "NoNewPrivs": "1",
        }
        assert "ENGINE_OWN" not in names.split() and "TMPDIR" in names.split()
    finally:
        await provider.purge(workspace.org_id, workspace.id)


@needs_an_account
async def test_an_account_release_ends_its_command_and_what_left_its_session_and_a_purge_its_files(
    account_root: Path, tmp_path: Path
) -> None:
    """The account's processes are the workspace's, wherever they run: a
    release ends the command still running and a process it left in a
    session of its own, outside the workspace's directory, and keeps the
    files, which a purge removes."""
    provider = WorkspaceAccountImpl(account_root, ACCOUNT)
    org, workspace_id = new_id(), new_id()
    workspace = await provider.prepare(org, workspace_id, spec(IsolationMode.ACCOUNT))
    transport = as_account(tmp_path / "records")
    script = (
        "(cd / && exec setsid sleep 300 </dev/null >/dev/null 2>&1) & echo $! > left.pid; "
        "echo $$ > command.pid; exec sleep 300"
    )
    command = asyncio.create_task(ran(transport, workspace, script, seconds=120))
    home = Path(workspace.location)
    for _ in range(100):
        if (home / "left.pid").exists() and (home / "command.pid").exists():
            break
        await asyncio.sleep(0.1)
    pids = [int((home / name).read_text()) for name in ("command.pid", "left.pid")]
    assert all(alive(pid) for pid in pids)
    await provider.release(workspace)
    result = await asyncio.wait_for(command, 30)
    assert result.exit_code != 0 and not result.timed_out
    assert await still_alive(*pids) == []
    assert (home / "left.pid").exists(), "a release keeps the files"
    await provider.purge(org, workspace_id)
    assert not home.parent.exists()


@needs_an_account
@pytest.mark.skipif(not proc_hides_others(), reason="/proc here shows every account's processes")
async def test_an_account_release_ends_what_it_left_where_proc_hides_it_from_this_process(
    account_root: Path, tmp_path: Path
) -> None:
    """Where `/proc` hides another account's processes from this one, a
    process the account left running is still ended by the release."""
    provider = WorkspaceAccountImpl(account_root, ACCOUNT)
    org, workspace_id = new_id(), new_id()
    workspace = await provider.prepare(org, workspace_id, spec(IsolationMode.ACCOUNT))
    try:
        script = "(cd / && exec setsid sleep 300 </dev/null >/dev/null 2>&1) & echo $!"
        result = await ran(as_account(tmp_path / "records"), workspace, script)
        assert result.exit_code == 0, result.stderr
        left = int(result.stdout.strip())
        assert alive(left)
        shown = await asyncio.to_thread(os.path.exists, f"/proc/{left}")
        assert not shown, "this process's /proc hides it"
        await provider.release(workspace)
        assert await still_alive(left) == []
    finally:
        await provider.purge(org, workspace_id)


@needs_an_account
async def test_an_account_purge_follows_no_link_the_account_planted_and_clears_what_it_closed(
    account_root: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "kept.txt").write_text("the engine's")
    before = (outside / "kept.txt").stat()
    provider = WorkspaceAccountImpl(account_root, ACCOUNT)
    workspace = await provider.prepare(new_id(), new_id(), spec(IsolationMode.ACCOUNT))
    plant = (
        f"ln -s {outside}/kept.txt file-link; ln -s {outside} dir-link; "
        "mkdir -p closed/deep; touch closed/deep/f; ln -s ../../file-link closed/deep/l; "
        "chmod 500 closed/deep; chmod 0 closed; touch $TMPDIR/scratch"
    )
    result = await ran(as_account(tmp_path / "records"), workspace, plant)
    assert result.exit_code == 0, result.stderr
    await provider.release(workspace)
    await provider.purge(workspace.org_id, workspace.id)
    assert not Path(workspace.location).parent.exists()
    after = (outside / "kept.txt").stat()
    assert (outside / "kept.txt").read_text() == "the engine's"
    assert (after.st_mode, after.st_uid, after.st_mtime) == (
        before.st_mode,
        before.st_uid,
        before.st_mtime,
    )
    assert [path.name for path in outside.iterdir()] == ["kept.txt"]


@needs_an_account
async def test_the_account_serves_one_workspace_at_a_time_and_a_released_one_is_closed_to_it(
    account_root: Path, tmp_path: Path
) -> None:
    provider = WorkspaceAccountImpl(account_root, ACCOUNT)
    transport = as_account(tmp_path / "records")
    first = await provider.prepare(new_id(), new_id(), spec(IsolationMode.ACCOUNT))
    second: Workspace | None = None
    try:
        await ran(transport, first, "echo first > notes.txt")
        with pytest.raises(IsolationRefused, match="one at a time"):
            await provider.prepare(new_id(), new_id(), spec(IsolationMode.ACCOUNT))
        await provider.release(first)
        second = await provider.prepare(new_id(), new_id(), spec(IsolationMode.ACCOUNT))
        reached = await ran(transport, second, f"cat {first.location}/notes.txt")
        assert reached.exit_code != 0 and "first" not in reached.stdout
        with pytest.raises(IsolationRefused, match="one at a time"):
            await provider.prepare(first.org_id, first.id, spec(IsolationMode.ACCOUNT))
    finally:
        if second is not None:
            await provider.release(second)
            await provider.purge(second.org_id, second.id)
        await provider.purge(first.org_id, first.id)
