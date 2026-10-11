"""A workspace base at the provider (ADR 1028). A provider that cannot
snapshot refuses a base, and the container provider refuses a setup egress
it does not enforce, both before anything is made. A container on a base
starts on the setup's whole filesystem; its files are the base's only when
its volume is new, so a workspace found again keeps its own, and gets the
setup's layer back.

The cases on real Docker are integration cases, skipped, with the reason,
where no Docker runs."""

import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from acme.infra.base import new_id
from acme.infra.docker import DockerReply, docker
from acme.infra.workspaces import (
    EgressMode,
    EgressPolicy,
    IsolationMode,
    IsolationRefused,
    IsolationSpec,
    Workspace,
    WorkspaceBase,
)
from acme.infra.workspaces.account import WorkspaceAccountImpl
from acme.infra.workspaces.container import WorkspaceContainerImpl
from acme.infra.workspaces.host import UNKEPT, WorkspaceHostImpl

NONE = EgressPolicy(mode=EgressMode.NONE)
OPEN = EgressPolicy(mode=EgressMode.OPEN)
IMAGE = "python:3.14-slim"
BASE = WorkspaceBase(image=IMAGE, setup=("echo layer > /opt/tool",), egress=OPEN)


def on(
    mode: IsolationMode, base: WorkspaceBase = BASE, egress: EgressPolicy = OPEN
) -> IsolationSpec:
    return IsolationSpec(mode=mode, egress=egress, base=base)


@pytest.mark.parametrize("base", [BASE, WorkspaceBase(image=IMAGE)], ids=["setup", "image"])
async def test_a_host_directory_and_an_account_refuse_a_base_and_make_nothing(
    tmp_path: Path, base: WorkspaceBase
) -> None:
    """A directory's commands write outside it, so no base's snapshot can
    start one, and it has no image: refused, before its directory is made."""
    for provider, mode in (
        (WorkspaceHostImpl(tmp_path / "workspaces"), IsolationMode.HOST),
        (WorkspaceAccountImpl(tmp_path / "workspaces", "acme-agent"), IsolationMode.ACCOUNT),
    ):
        with pytest.raises(IsolationRefused, match="cannot start from a base") as refused:
            await provider.prepare(new_id(), new_id(), on(mode, base))
        assert UNKEPT in refused.value.message
        assert not refused.value.clears
    assert not (tmp_path / "workspaces").exists()


async def test_the_container_provider_refuses_what_it_cannot_hold_before_docker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A setup's allowlist needs a proxy this provider does not run, and a
    base with setup never starts on its bare image: both are refused before
    any Docker command runs."""
    calls: list[tuple[str, ...]] = []

    async def recorded(*args: str, **_: object) -> DockerReply:
        calls.append(args)
        return DockerReply(0, b"", b"")

    monkeypatch.setattr("acme.infra.workspaces.container.docker", recorded)
    provider = WorkspaceContainerImpl(IMAGE, timedelta(seconds=5))
    listed = BASE.model_copy(
        update={"egress": EgressPolicy(mode=EgressMode.ALLOWLIST, hosts=("pypi.org",))}
    )
    with pytest.raises(IsolationRefused, match="setup cannot hold egress to allowlist"):
        await provider.prepare(new_id(), new_id(), on(IsolationMode.CONTAINER, listed, NONE))
    with pytest.raises(IsolationRefused, match="starts from the base's snapshot alone"):
        await provider.prepare(new_id(), new_id(), on(IsolationMode.CONTAINER, egress=NONE))
    assert calls == []


def docker_runs() -> bool:
    try:
        reply = subprocess.run(["docker", "version"], capture_output=True, timeout=20)
    except FileNotFoundError, subprocess.TimeoutExpired:
        return False
    return reply.returncode == 0


async def inside(workspace: Workspace, *argv: str) -> tuple[int | None, str]:
    reply = await docker("exec", workspace.location, *argv, bound=timedelta(seconds=120))
    return reply.code, reply.stdout.decode()


@pytest.mark.integration
@pytest.mark.skipif(not docker_runs(), reason="needs a local Docker")
async def test_a_container_on_a_base_starts_on_its_setup_and_keeps_its_own_files() -> None:
    """The setup's layer is in every container on the base, and its files
    seed a new volume alone: a workspace found again after a release keeps
    what it wrote, and gets the setup's layer back."""
    provider = WorkspaceContainerImpl(IMAGE, timedelta(seconds=300))
    org, build_id, workspace_id = new_id(), new_id(), new_id()
    build = await provider.prepare(
        org, build_id, on(IsolationMode.CONTAINER, WorkspaceBase(image=IMAGE), NONE)
    )
    try:
        setup = "echo layer > /opt/tool && echo seed > /workspace/seed && rm /etc/issue"
        assert (await inside(build, "sh", "-c", setup))[0] == 0
        base = await provider.snapshot(build)
    finally:
        await provider.purge(org, build_id)
    spec = on(IsolationMode.CONTAINER, egress=NONE)
    try:
        workspace = await provider.prepare(org, workspace_id, spec, base=base)
        assert await inside(workspace, "cat", "/opt/tool", "/workspace/seed") == (
            0,
            "layer\nseed\n",
        )
        assert (await inside(workspace, "test", "-e", "/etc/issue"))[0] == 1, "removed again"
        await inside(workspace, "sh", "-c", "echo mine > /workspace/seed && rm /opt/tool")
        await provider.release(workspace)

        again = await provider.prepare(org, workspace_id, spec, base=base)

        assert await inside(again, "cat", "/opt/tool", "/workspace/seed") == (0, "layer\nmine\n")
    finally:
        await provider.purge(org, workspace_id)
