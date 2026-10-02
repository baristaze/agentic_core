"""Isolation is chosen up front and never weakened. A provider meets a spec
whole or refuses it, before it creates anything, and never hands back a
weaker place: not the host directory for a container, and not a container
with open egress for one that asked for none. A host directory's release
ends what its commands left running there."""

import asyncio
import contextlib
import os
import re
import signal
from datetime import timedelta
from pathlib import Path

import pytest

from acme.infra.base import new_id
from acme.infra.docker import DockerReply
from acme.infra.impl.configured import InfraConfiguredImpl
from acme.infra.impl.settings import InfraSettings
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
from acme.infra.workspaces.container import WorkspaceContainerImpl
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
    await provider.purge(again)
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


async def test_a_host_purge_ends_what_runs_there_before_its_files_go(tmp_path: Path) -> None:
    provider = WorkspaceHostImpl(tmp_path / "workspaces")
    workspace = await provider.prepare(new_id(), new_id(), spec(IsolationMode.HOST))
    left = await left_behind(Path(workspace.location))
    try:
        await provider.purge(workspace)
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
    Docker does. It keeps every command it is given."""

    def __init__(self) -> None:
        self.labels: dict[str, str] | None = None  # the running container's, when one runs
        self.calls: list[tuple[str, ...]] = []

    async def __call__(self, *args: str, **_: object) -> DockerReply:
        self.calls.append(args)
        if args[0] == "run":
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
    for mode in (IsolationMode.VM, IsolationMode.CONTAINER, IsolationMode.HOST):
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
