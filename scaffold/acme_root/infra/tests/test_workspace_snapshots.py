"""A workspace a provider snapshots is the one a restore gives back: every
file its commands wrote, in the volume and outside it, a package installed
into the image's own directories included, and nothing of what they
removed. A provider that cannot snapshot says so before anything runs, and
a spec that needs it is refused rather than met by a cache.

The cases on real Docker are integration cases, skipped, with the reason,
where no Docker runs."""

import io
import json
import subprocess
import tarfile
from datetime import timedelta
from pathlib import Path

import pytest

from acme.infra.base import new_id
from acme.infra.docker import docker
from acme.infra.workspaces import (
    Durability,
    EgressMode,
    EgressPolicy,
    IsolationMode,
    IsolationRefused,
    IsolationSpec,
    SnapshotRefused,
    Workspace,
    WorkspaceLost,
)
from acme.infra.workspaces.account import WorkspaceAccountImpl
from acme.infra.workspaces.container import (
    FORMAT,
    WorkspaceContainerImpl,
    _changes,
    container_name,
)
from acme.infra.workspaces.host import UNKEPT, WorkspaceHostImpl
from acme.infra.workspaces.twin import WorkspaceNullImpl, WorkspaceTwinImpl

NONE = EgressPolicy(mode=EgressMode.NONE)
OPEN = EgressPolicy(mode=EgressMode.OPEN)
IMAGE = "python:3.14-slim"


def kept(mode: IsolationMode, egress: EgressPolicy = OPEN) -> IsolationSpec:
    return IsolationSpec(mode=mode, egress=egress, durability=Durability.SNAPSHOT)


async def test_a_host_directory_refuses_a_spec_that_asks_for_snapshots_and_makes_nothing(
    tmp_path: Path,
) -> None:
    """A directory's commands write outside it, so a session that needs its
    workspace kept whole is refused at prepare, with the reason, before any
    command runs and before its directory is made: never a cache in its
    stead."""
    provider = WorkspaceHostImpl(tmp_path / "workspaces")
    with pytest.raises(IsolationRefused, match="cannot be snapshotted") as refused:
        await provider.prepare(new_id(), new_id(), kept(IsolationMode.HOST))
    assert UNKEPT in refused.value.message
    assert not refused.value.clears
    assert not (tmp_path / "workspaces").exists()
    cached = IsolationSpec(mode=IsolationMode.HOST, egress=OPEN)
    workspace = await provider.prepare(new_id(), new_id(), cached)
    with pytest.raises(SnapshotRefused, match="cannot be snapshotted"):
        await provider.snapshot(workspace)
    with pytest.raises(IsolationRefused, match="cannot start from a snapshot"):
        await provider.prepare(workspace.org_id, new_id(), cached, snapshot=b"archive")


async def test_an_account_workspace_refuses_a_spec_that_asks_for_snapshots_before_it_switches(
    tmp_path: Path,
) -> None:
    """The account provider refuses the same way, before it checks the host
    or the account, so even a host that could switch never runs a command
    for a session whose workspace it cannot keep."""
    provider = WorkspaceAccountImpl(tmp_path / "workspaces", "acme-agent")
    with pytest.raises(IsolationRefused, match="cannot be snapshotted") as refused:
        await provider.prepare(new_id(), new_id(), kept(IsolationMode.ACCOUNT))
    assert UNKEPT in refused.value.message
    assert not (tmp_path / "workspaces").exists()
    workspace = Workspace(
        id=new_id(),
        org_id=new_id(),
        spec=IsolationSpec(mode=IsolationMode.ACCOUNT, egress=OPEN),
        location=str(tmp_path),
    )
    with pytest.raises(SnapshotRefused, match="cannot be snapshotted"):
        await provider.snapshot(workspace)


async def test_the_null_provider_snapshots_nothing_and_the_twin_one_only_a_live_workspace() -> None:
    absent = Workspace.absent(new_id(), new_id())
    with pytest.raises(SnapshotRefused):
        await WorkspaceNullImpl().snapshot(absent)
    twin = WorkspaceTwinImpl()
    spec = kept(IsolationMode.TWIN)
    workspace = await twin.prepare(absent.org_id, absent.id, spec)
    twin.files[workspace.id] = b"files"
    assert await twin.snapshot(workspace) == b"files"
    await twin.release(workspace)
    with pytest.raises(SnapshotRefused, match="no instance"):
        await twin.snapshot(workspace)
    other = await twin.prepare(absent.org_id, new_id(), spec, snapshot=b"files")
    assert twin.files[other.id] == b"files"


def test_a_snapshot_keeps_what_commands_changed_and_never_what_docker_or_the_kernel_writes() -> (
    None
):
    diff = "\n".join(
        [
            "C /usr",
            "A /usr/sbin/docker-init",
            "C /etc",
            "A /etc/hostname",
            "D /etc/issue",
            "A /workspace",
            "A /workspace/inside.txt",
            "A /opt/x",
            "C /dev/null",
            "A /proc/1",
        ]
    )
    kept_paths, deleted = _changes(diff)
    assert kept_paths == {"/usr", "/etc", "/opt/x"}
    assert deleted == ("/etc/issue",)


def archive(manifest: object, extra: dict[str, bytes] | None = None) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as written:
        for name, data in {"manifest.json": json.dumps(manifest).encode(), **(extra or {})}.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            written.addfile(member, io.BytesIO(data))
    return out.getvalue()


IMAGE_ID = "sha256:" + "0" * 64


@pytest.mark.parametrize(
    ("snapshot", "says"),
    [
        (b"not an archive", "no container's"),
        (archive({"format": "acme.vm-snapshot/1", "image": IMAGE_ID, "deleted": []}), "not a"),
        (archive({"format": FORMAT, "image": "python:3.14-slim", "deleted": []}), "by its id"),
        (archive({"format": FORMAT, "image": "--privileged", "deleted": []}), "by its id"),
        (archive({"format": FORMAT, "image": IMAGE_ID, "deleted": ["etc"]}), "removed path"),
    ],
    ids=["noise", "another-provider", "a-tag", "an-option", "a-relative-path"],
)
async def test_a_snapshot_the_container_provider_did_not_write_loses_the_workspace_before_docker(
    snapshot: bytes, says: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An archive of another provider's, or one that names its image by a tag
    that may have moved, is never brought in: the workspace is lost, loudly,
    and no Docker command but the reach check runs."""
    calls: list[tuple[str, ...]] = []

    async def recorded(*args: str, **_: object) -> object:
        calls.append(args)
        from acme.infra.docker import DockerReply

        return DockerReply(0, b"29.0.0\n", b"")

    monkeypatch.setattr("acme.infra.workspaces.container.docker", recorded)
    provider = WorkspaceContainerImpl(IMAGE, timedelta(seconds=5))
    with pytest.raises(WorkspaceLost, match=says):
        await provider.prepare(new_id(), new_id(), kept(IsolationMode.CONTAINER), snapshot=snapshot)
    assert [call[0] for call in calls] == ["version"]


def docker_runs() -> bool:
    try:
        reply = subprocess.run(["docker", "version"], capture_output=True, timeout=20)
    except FileNotFoundError, subprocess.TimeoutExpired:
        return False
    return reply.returncode == 0


WHEEL = r"""
import base64, hashlib, zipfile
files = {
    "demo_kept/__init__.py": b"VALUE = 'kept'\n",
    "demo_kept-1.0.dist-info/METADATA": b"Metadata-Version: 2.1\nName: demo-kept\nVersion: 1.0\n",
    "demo_kept-1.0.dist-info/WHEEL": (
        b"Wheel-Version: 1.0\nGenerator: hand\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    ),
}
def digest(data):
    raw = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"sha256={raw}"
record = "".join(f"{name},{digest(data)},{len(data)}\n" for name, data in files.items())
record += "demo_kept-1.0.dist-info/RECORD,,\n"
with zipfile.ZipFile("/tmp/demo_kept-1.0-py3-none-any.whl", "w") as wheel:
    for name, data in files.items():
        wheel.writestr(name, data)
    wheel.writestr("demo_kept-1.0.dist-info/RECORD", record)
"""

TREE = r"""
import hashlib, os, stat
whole = hashlib.sha256()
for root, dirs, files in os.walk("/"):
    dirs[:] = sorted(d for d in dirs if os.path.join(root, d) not in ("/proc", "/sys", "/dev"))
    for name in sorted(dirs + files):
        path = os.path.join(root, name)
        st = os.lstat(path)
        line = f"{path} {stat.S_IFMT(st.st_mode)} {stat.S_IMODE(st.st_mode)} {st.st_uid} {st.st_gid}"
        if stat.S_ISREG(st.st_mode):
            try:
                with open(path, "rb") as held:
                    line += " " + hashlib.file_digest(held, "sha256").hexdigest()
            except OSError as error:
                line += f" unread {error.errno}"
        elif stat.S_ISLNK(st.st_mode):
            line += " " + os.readlink(path)
        whole.update(line.encode() + b"\n")
print(whole.hexdigest())
"""


async def inside(workspace: Workspace, *argv: str) -> str:
    reply = await docker("exec", workspace.location, *argv, bound=timedelta(seconds=120))
    assert reply.ok, reply.stderr.decode()
    return reply.stdout.decode()


@pytest.mark.integration
@pytest.mark.skipif(not docker_runs(), reason="needs a local Docker")
async def test_a_container_restored_from_its_snapshot_holds_the_whole_tree_it_kept() -> None:
    """A file in the volume, a file outside it, a package installed into the
    image's site-packages, and a file removed from the image: snapshotted,
    purged, and prepared from the snapshot, the tree hashes the same,
    `/proc`, `/sys`, and `/dev` aside."""
    provider = WorkspaceContainerImpl(IMAGE, timedelta(seconds=300))
    org, workspace_id = new_id(), new_id()
    spec = kept(IsolationMode.CONTAINER, NONE)
    workspace = await provider.prepare(org, workspace_id, spec)
    try:
        await inside(workspace, "sh", "-c", "echo in > /workspace/inside.txt")
        await inside(workspace, "sh", "-c", "mkdir -p /opt/kept && echo out > /opt/kept/out.txt")
        await inside(workspace, "rm", "/etc/issue")
        await inside(workspace, "python", "-c", WHEEL)
        await inside(
            workspace,
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--root-user-action=ignore",
            "--disable-pip-version-check",
            "/tmp/demo_kept-1.0-py3-none-any.whl",
        )
        before = await inside(workspace, "python", "-c", TREE)
        snapshot = await provider.snapshot(workspace)
        await provider.purge(org, workspace_id)
        name = container_name(workspace_id)
        assert not (await docker("inspect", name, bound=timedelta(seconds=20))).ok
        assert not (await docker("volume", "inspect", name, bound=timedelta(seconds=20))).ok

        restored = await provider.prepare(org, workspace_id, spec, snapshot=snapshot)
        assert await inside(restored, "python", "-c", TREE) == before
        assert await inside(restored, "cat", "/workspace/inside.txt", "/opt/kept/out.txt") == (
            "in\nout\n"
        )
        imported = "import demo_kept; print(demo_kept.VALUE)"
        assert await inside(restored, "python", "-c", imported) == "kept\n"
        assert (
            await docker(
                "exec", restored.location, "test", "-e", "/etc/issue", bound=timedelta(seconds=20)
            )
        ).code == 1
    finally:
        await provider.purge(org, workspace_id)


@pytest.mark.integration
@pytest.mark.skipif(not docker_runs(), reason="needs a local Docker")
async def test_a_snapshot_is_taken_from_a_live_workspace_and_leaves_it_running() -> None:
    """A released workspace has no writable layer left to keep: its snapshot
    is refused, never taken from its volume alone. A live one keeps running
    once its snapshot is taken, and a restore of another workspace from it
    leaves the first untouched."""
    provider = WorkspaceContainerImpl(IMAGE, timedelta(seconds=300))
    org, first, second = new_id(), new_id(), new_id()
    spec = kept(IsolationMode.CONTAINER, NONE)
    workspace = await provider.prepare(org, first, spec)
    try:
        await inside(workspace, "sh", "-c", "echo parent > /workspace/shared.txt")
        snapshot = await provider.snapshot(workspace)
        assert await inside(workspace, "cat", "/workspace/shared.txt") == "parent\n"
        child = await provider.prepare(org, second, spec, snapshot=snapshot)
        await inside(child, "sh", "-c", "echo child > /workspace/own.txt")
        assert await inside(child, "cat", "/workspace/shared.txt") == "parent\n"
        missing = await docker(
            "exec",
            workspace.location,
            "test",
            "-e",
            "/workspace/own.txt",
            bound=timedelta(seconds=20),
        )
        assert missing.code == 1, "a write in the restored workspace never reaches the first"
        await provider.release(workspace)
        with pytest.raises(SnapshotRefused, match="no instance"):
            await provider.snapshot(workspace)
    finally:
        await provider.purge(org, first)
        await provider.purge(org, second)
