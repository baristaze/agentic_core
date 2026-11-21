"""A VM workspace: a VM per workspace on the VMs it is given, Docker
inside it (ADR 1029). Over the VMs' twin, a VM is a folder of
this host, so the provider and its transport run whole in the unit suite
(the transport's contract is in `test_transports`): the provider prepares,
releases, packs its directory, restores it on a new VM, keeps a base,
and purges. A spec the VMs cannot hold is refused before a VM starts: one
whose probe finds no hypervisor, an egress they cannot close, a limit they
cannot enforce, and a base their store cannot keep encrypted. A session's
snapshot keeps the directory alone (ADR 1045), measured against its bound
before it is packed, and a pack that fails or outlasts its time leaves
nothing behind. A restore starts its new VM under the next of the
workspace's names, and the VM it replaces goes only once the new one holds
the whole directory. A base is a disk, held to its digest before a VM
starts on it.

Over Lima, where this host runs it, a VM workspace runs Docker, and its
directory comes back on a new VM whole, a file written as root included,
while what its commands made outside it, such as an image Docker built,
comes from the base again. Nothing of the engine's environment reaches the
guest. These cases are integration cases, skipped,
with the reason, where Lima or a hypervisor is missing; every VM they
make is named under `ACME_VM_PREFIX` and destroyed at their end."""

import io
import json
import os
import shutil
import tarfile
import time
from collections.abc import AsyncIterator, Iterable
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from annotated_types import MaxLen

from acme.infra.agent_workspaces import (
    AgentWorkspace,
    AgentWorkspaceBase,
    AgentWorkspaceLost,
    AgentWorkspaceProviderInterface,
    DirectoryPack,
    Durability,
    EgressMode,
    EgressPolicy,
    IsolationMode,
    IsolationRefused,
    IsolationSpec,
    ResourceLimits,
    SnapshotRefused,
    packs,
)
from acme.infra.agent_workspaces.vm import AgentWorkspaceVmImpl, longest_name, vm_name, vm_names
from acme.infra.base import new_id, utcnow
from acme.infra.exceptions import BackendFailed, InfraValidationFailed
from acme.infra.impl.settings import InfraSettings
from acme.infra.secrets.local import SecretsLocalImpl
from acme.infra.transports import CommandSpec, TransportInterface, secret_held
from acme.infra.transports.broker import BrokerTwinImpl
from acme.infra.transports.twin import RecordSealTwin
from acme.infra.transports.vm import TransportVmImpl
from acme.infra.vms import Channel, VmReply, VmSpec, VmState
from acme.infra.vms.lima import (
    SOCKET,
    VmsLimaImpl,
    hypervisor,
    lima_environment,
    name_refused,
    template,
)
from acme.infra.vms.twin import VmsNullImpl, VmsTwinImpl

PREFIX = "acme-test-"
ENGINE_CREDENTIAL = "ACME_DATABASE_URL"
SEAL = RecordSealTwin().seal
OPEN = EgressPolicy(mode=EgressMode.OPEN)
NONE = EgressPolicy(mode=EgressMode.NONE)
TIMEOUT = timedelta(seconds=60)
BOUND = 1 << 30
"""A bound no case's directory comes near."""


def vm(
    egress: EgressPolicy = OPEN,
    durability: Durability = Durability.CACHE,
    **limits: float,
) -> IsolationSpec:
    return IsolationSpec(
        mode=IsolationMode.VM,
        egress=egress,
        limits=ResourceLimits.model_validate(limits),
        durability=durability,
    )


def over_twin(tmp_path: Path, **vms: object) -> tuple[AgentWorkspaceVmImpl, VmsTwinImpl]:
    twin = VmsTwinImpl(tmp_path / "vms", **vms)  # type: ignore[arg-type]
    return AgentWorkspaceVmImpl(twin, "twin-image", PREFIX, TIMEOUT), twin


def transport_over(vms: VmsTwinImpl | VmsLimaImpl, tmp_path: Path, org: UUID) -> TransportVmImpl:
    secrets = SecretsLocalImpl(None, {})
    return TransportVmImpl(tmp_path / "records", secrets, BrokerTwinImpl(), vms, TIMEOUT)


def command(*argv: str, seconds: float = 600) -> CommandSpec:
    return CommandSpec(
        argv=argv, key=new_id(), epoch=1, deadline=utcnow() + timedelta(seconds=seconds)
    )


async def written(
    transport: TransportInterface, workspace: AgentWorkspace, script: str
) -> tuple[int | None, str]:
    ran = await transport.run(workspace, command("sh", "-c", script), seal=SEAL)
    return ran.exit_code, ran.stdout + ran.stderr


async def read_out(
    provider: AgentWorkspaceVmImpl, pack: DirectoryPack, part_bytes: int
) -> list[bytes]:
    return [part async for part in provider.read(pack, part_bytes)]


async def streamed(parts: Iterable[bytes], fail_after: int | None = None) -> AsyncIterator[bytes]:
    for index, part in enumerate(parts):
        if index == fail_after:
            raise AgentWorkspaceLost("a part does not match its hash")
        yield part


def members(archive: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        return {
            member.name.removeprefix("./"): (tar.extractfile(member) or io.BytesIO()).read()
            for member in tar.getmembers()
            if member.isfile()
        }


async def test_a_vm_workspace_keeps_its_directory_alone_and_a_restore_starts_on_its_base(
    tmp_path: Path,
) -> None:
    """Its pack is one archive of the directory, measured before any byte
    leaves, and read out in parts in order. The VM keeps running. A
    restore starts a new VM on the base and fills the directory from the
    parts, replacing what it held; what the commands wrote outside the
    directory is gone. The pack, dropped, is gone; a purge takes the VM."""
    provider, twin = over_twin(tmp_path)
    org, agent_workspace_id = new_id(), new_id()
    transport = transport_over(twin, tmp_path, org)
    workspace = await provider.prepare(org, agent_workspace_id, vm(durability=Durability.SNAPSHOT))
    assert provider.keeps_directory()
    script = "echo kept > kept.txt; mkdir -p deep; head -c 9000 /dev/zero > deep/z; echo x > ../out"
    assert await written(transport, workspace, script) == (0, "")

    pack = await provider.pack(workspace, BOUND)

    parts = await read_out(provider, pack, 4096)
    assert pack.location == workspace.location and pack.size == sum(map(len, parts))
    assert [len(part) for part in parts[:-1]] == [4096] * (len(parts) - 1) and len(parts) > 2
    assert members(b"".join(parts)) == {"kept.txt": b"kept\n", "deep/z": bytes(9000)}
    assert await twin.state(workspace.location) is VmState.RUNNING, "it keeps its instance"
    await provider.drop(pack)
    gone = f"test -e {pack.path} || echo gone"
    assert await written(transport, workspace, gone) == (0, "gone\n"), "the pack"
    await provider.drop(pack)
    assert await written(transport, workspace, "echo later > kept.txt; touch new.txt") == (0, "")

    restored = await provider.restore(org, agent_workspace_id, vm(), streamed(parts))
    outside = "cat kept.txt; ls; test -e ../out || echo gone"
    assert await written(transport, restored, outside) == (
        0,
        "kept\ndeep\nkept.txt\ngone\n",
    )
    names = vm_names(PREFIX, agent_workspace_id)
    assert (workspace.location, restored.location) == names[:2]
    assert await twin.names(names[0]) == [restored.location], "the VM it replaced, gone"
    await provider.purge(org, agent_workspace_id)
    assert await twin.names(vm_name(PREFIX, agent_workspace_id)) == [], "the VM"


async def test_a_restore_that_fails_part_way_destroys_its_new_vm_alone(tmp_path: Path) -> None:
    """A part that does not match ends the restore with its error, and a
    stream that is no archive ends it with the unpack's: either way the new
    VM is destroyed, never left half-restored, and the VM it would replace
    stands as it was, its files with it. Where none stood, none stands. A
    whole archive is no VM's to start from."""
    provider, twin = over_twin(tmp_path)
    org, agent_workspace_id = new_id(), new_id()
    names = vm_names(PREFIX, agent_workspace_id)
    transport = transport_over(twin, tmp_path, org)
    workspace = await provider.prepare(org, agent_workspace_id, vm())
    await written(transport, workspace, "head -c 20000 /dev/urandom > state.bin")
    held = (twin.vms[workspace.location].folder / "workspace" / "state.bin").read_bytes()
    pack = await provider.pack(workspace, BOUND)
    parts = await read_out(provider, pack, 4096)
    await provider.drop(pack)
    await provider.release(workspace)

    with pytest.raises(AgentWorkspaceLost, match="does not match"):
        await provider.restore(org, agent_workspace_id, vm(), streamed(parts, fail_after=2))
    with pytest.raises(BackendFailed, match="unpack"):
        await provider.restore(org, agent_workspace_id, vm(), streamed([b"no archive" * 100]))

    assert [call for call in twin.calls if call[0] == "destroy"] == [("destroy", names[1])] * 2
    assert await twin.names(names[0]) == [workspace.location], "the VM it would replace"
    again = await provider.prepare(org, agent_workspace_id, vm())
    assert again.location == workspace.location
    assert (twin.vms[again.location].folder / "workspace" / "state.bin").read_bytes() == held
    await provider.purge(org, agent_workspace_id)
    with pytest.raises(AgentWorkspaceLost, match="does not match"):
        await provider.restore(org, agent_workspace_id, vm(), streamed(parts, fail_after=2))
    assert await twin.names(names[0]) == [], "none stood, and none stands"
    with pytest.raises(AgentWorkspaceLost, match="not a whole archive"):
        await provider.prepare(org, agent_workspace_id, vm(), snapshot=b"a container's tar")


async def test_a_vm_a_restore_left_cut_off_is_never_the_workspace(tmp_path: Path) -> None:
    """Of two VMs under the workspace's names, the later in turn is a
    restore's that was cut off before the VM it replaces went: the next
    prepare destroys it and finds the other, the names taken in turn."""
    provider, twin = over_twin(tmp_path)
    org, agent_workspace_id = new_id(), new_id()
    names = vm_names(PREFIX, agent_workspace_id)
    spec = VmSpec(image="twin-image", cpus=None, memory_mb=None, egress_open=True)
    for earlier, later in ((0, 1), (2, 0)):
        await twin.launch(names[earlier], spec)
        await twin.launch(names[later], spec)

        found = await provider.prepare(org, agent_workspace_id, vm())

        assert found.location == names[earlier]
        assert await twin.names(names[0]) == [names[earlier]], "the cut-off VM, destroyed"
        await provider.purge(org, agent_workspace_id)


def shimmed(tmp_path: Path, tar: str) -> tuple[Channel, Path, Path]:
    """A channel into a folder of this host whose `tar` runs `tar` and whose
    `sudo` refuses, so a pack runs as this user; the directory it packs;
    and the folder its packs are written in."""
    shims, guest, temporary = tmp_path / "shims", tmp_path / "guest", tmp_path / "var-tmp"
    for folder in (shims, guest / "workspace", temporary):
        folder.mkdir(parents=True)
    for name, body in (("sudo", "exit 1"), ("tar", tar)):
        (shims / name).write_text(f"#!/bin/sh\n{body}\n")
        (shims / name).chmod(0o755)
    path = f"{shims}:{os.environ.get('PATH', '/usr/bin:/bin')}"
    channel = Channel(
        argv=("sh", "-c", 'cd "$0" || exit 255; exec "$@"', str(guest)), env={"PATH": path}
    )
    return channel, guest / "workspace", temporary


async def test_a_directory_past_the_bound_is_refused_by_its_size_before_it_is_packed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel, directory, temporary = shimmed(tmp_path, f'touch "{tmp_path}/tar-ran"; exit 2')
    monkeypatch.setattr(packs, "TEMPORARY", str(temporary))
    (directory / "deep").mkdir()
    (directory / "a.bin").write_bytes(bytes(6000))
    (directory / "deep" / "b.bin").write_bytes(bytes(5000))

    with pytest.raises(SnapshotRefused, match="holds 11000 bytes, past the bound of 10000"):
        await packs.pack(channel, "workspace", bound=10_000, within=TIMEOUT, stall=TIMEOUT)

    assert not (tmp_path / "tar-ran").exists(), "never packed"
    assert list(temporary.iterdir()) == []


@pytest.mark.parametrize(
    ("tar", "why"),
    [
        ("head -c 5000 /dev/zero; exit 2", "exited 2"),
        ("exec 2>/dev/null; head -c 5000 /dev/zero; sleep 2", "did not end within 0.5 seconds"),
    ],
    ids=["fails", "outlasts its time"],
)
async def test_a_pack_that_fails_or_outlasts_its_time_leaves_no_pack_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tar: str, why: str
) -> None:
    """The pack's path is named before tar starts, so a tar that fails, or
    one still writing when the pack's own time runs out, leaves no file."""
    channel, directory, temporary = shimmed(tmp_path, tar)
    monkeypatch.setattr(packs, "TEMPORARY", str(temporary))
    (directory / "a.bin").write_bytes(bytes(100))
    started = time.monotonic()

    with pytest.raises(BackendFailed, match=why):
        await packs.pack(
            channel, "workspace", bound=10_000, within=timedelta(seconds=0.5), stall=TIMEOUT
        )

    assert time.monotonic() - started < 2, "bounded by its own time"
    assert list(temporary.iterdir()) == [], "no pack left behind"


async def test_a_base_kept_under_its_own_workspace_outlives_the_purge_of_its_build(
    tmp_path: Path,
) -> None:
    """A base's disk is kept under its own workspace: the build's purge
    leaves it, a copy asked again is the one made, a VM starts on it once
    it holds its digest, and the purge of its own workspace takes it."""
    provider, twin = over_twin(tmp_path)
    org, build, kept_under = new_id(), new_id(), new_id()
    transport = transport_over(twin, tmp_path, org)
    workspace = await provider.prepare(org, build, vm(), building=True)
    await written(transport, workspace, "echo set-up > state.txt")
    snapshot = await provider.snapshot(workspace)

    base = await provider.keep(snapshot, org, kept_under)
    assert await provider.keep(snapshot, org, kept_under) == base, "made once"
    await provider.purge(org, build)
    spec = vm().model_copy(update={"base": AgentWorkspaceBase(image="twin-image", setup=("true",))})
    on_base = await provider.prepare(org, new_id(), spec, base=base)
    assert await written(transport, on_base, "cat state.txt") == (0, "set-up\n")

    disk = twin.vms[json.loads(base)["name"]].folder
    (disk / "workspace" / "planted.txt").write_text("not the setup's")
    with pytest.raises(AgentWorkspaceLost, match="does not match its digest"):
        await provider.prepare(org, new_id(), spec, base=base)
    with pytest.raises(AgentWorkspaceLost, match="another tenant"):
        await provider.prepare(new_id(), new_id(), spec, base=base)
    await provider.purge(org, kept_under)
    assert await twin.names(vm_name(PREFIX, kept_under)) == []


async def test_a_machine_found_again_keeps_its_disk_and_a_new_one_starts_on_the_base(
    tmp_path: Path,
) -> None:
    provider, twin = over_twin(tmp_path)
    org = new_id()
    transport = transport_over(twin, tmp_path, org)
    setup = vm(durability=Durability.SNAPSHOT).model_copy(
        update={"base": AgentWorkspaceBase(image="twin-image")}
    )
    builder = await provider.prepare(org, new_id(), setup)
    await written(transport, builder, "echo from-the-base > base.txt")
    base = await provider.keep(await provider.snapshot(builder), org, new_id())
    spec = vm().model_copy(
        update={"base": AgentWorkspaceBase(image="twin-image", setup=("echo set up",))}
    )

    fresh = await provider.prepare(org, new_id(), spec, base=base)
    assert await written(transport, fresh, "cat base.txt") == (0, "from-the-base\n")
    await written(transport, fresh, "echo own > base.txt")
    await provider.release(fresh)
    again = await provider.prepare(org, fresh.id, spec, base=base)
    assert await written(transport, again, "cat base.txt") == (0, "own\n")
    with pytest.raises(IsolationRefused, match="base's snapshot alone"):
        await provider.prepare(org, new_id(), spec)


async def test_a_machine_with_no_hypervisor_refuses_the_workspace_before_any_command(
    tmp_path: Path,
) -> None:
    provider, twin = over_twin(tmp_path, hypervisor="this host offers no hypervisor")

    with pytest.raises(IsolationRefused, match="this host offers no hypervisor") as refused:
        await provider.prepare(new_id(), new_id(), vm())

    assert not refused.value.clears
    assert twin.calls == [("probe",)], "probed, and nothing made or run"


async def test_egress_the_machines_cannot_close_is_refused_before_a_machine_starts(
    tmp_path: Path,
) -> None:
    """VMs that cannot close a guest's egress refuse a workspace that must
    reach nothing, and a base whose setup must; an allowlist, a process
    limit, and part of a cpu are refused by every VM. None of it starts
    a VM. VMs that can close egress meet it."""
    provider, twin = over_twin(tmp_path, closes_egress=False)
    closed_setup = vm().model_copy(
        update={"base": AgentWorkspaceBase(image="twin-image", setup=("true",), egress=NONE)}
    )
    allowlist = EgressPolicy(mode=EgressMode.ALLOWLIST, hosts=("pypi.org",))
    for refused, why in (
        (vm(NONE), "egress to none"),
        (closed_setup, "setup cannot hold egress to none"),
        (vm(allowlist), "egress to allowlist"),
        (vm(processes=64), "cannot enforce processes"),
        (vm(cpus=1.5), "whole cpus"),
    ):
        with pytest.raises(IsolationRefused, match=why):
            await provider.prepare(new_id(), new_id(), refused)
    assert twin.calls == [], "no VM was asked for"

    closing, closes = over_twin(tmp_path / "closing", closes_egress=True)
    await closing.prepare(new_id(), new_id(), vm(NONE, cpus=2, memory_mb=2048))
    (started,) = closes.vms.values()
    assert started.spec == VmSpec(image="twin-image", cpus=2, memory_mb=2048, egress_open=False)


async def test_a_store_not_encrypted_at_rest_keeps_no_base(tmp_path: Path) -> None:
    """The store keeps a base's disk alone: without it encrypted at rest, a
    base with setup, or a base's build, is refused before a VM starts,
    and a base's snapshot before anything stops. A workspace kept by
    snapshots is met: its directory is sealed by its caller."""
    provider, twin = over_twin(tmp_path, encrypted=False)
    based = vm().model_copy(
        update={"base": AgentWorkspaceBase(image="twin-image", setup=("true",))}
    )
    with pytest.raises(IsolationRefused, match=r"base with setup: .* not encrypted at rest"):
        await provider.prepare(new_id(), new_id(), based, base=b"{}")
    with pytest.raises(IsolationRefused, match=r"base cannot be kept: .* not encrypted at rest"):
        await provider.prepare(new_id(), new_id(), vm(), building=True)
    assert twin.calls == []

    workspace = await provider.prepare(new_id(), new_id(), vm(durability=Durability.SNAPSHOT))
    with pytest.raises(SnapshotRefused, match="not encrypted at rest"):
        await provider.snapshot(workspace)
    assert ("stop", workspace.location) not in twin.calls
    pack = await provider.pack(workspace, BOUND)
    assert pack.size > 0
    await provider.drop(pack)


async def test_a_released_workspace_holds_no_instance_and_refuses_a_snapshot(
    tmp_path: Path,
) -> None:
    provider, twin = over_twin(tmp_path)
    workspace = await provider.prepare(new_id(), new_id(), vm())
    await provider.release(workspace)

    assert await twin.state(workspace.location) is VmState.STOPPED
    with pytest.raises(SnapshotRefused, match="holds no instance"):
        await provider.snapshot(workspace)
    with pytest.raises(SnapshotRefused, match="holds no instance"):
        await provider.pack(workspace, BOUND)


async def test_a_disks_secret_value_is_found_by_the_scan_of_what_the_snapshot_holds(
    tmp_path: Path,
) -> None:
    """A base's archive names the disk; what it holds is read from the
    disk, so a secret's value a setup command wrote anywhere on it, encoded
    or raw, is found by the scan every base takes before it is kept."""
    provider, twin = over_twin(tmp_path)
    org = new_id()
    transport = transport_over(twin, tmp_path, org)
    workspace = await provider.prepare(org, new_id(), vm())
    secret = {"SERVICE_TOKEN": "tok-0123456789abcdef"}
    clean = await provider.snapshot(workspace)
    assert await secret_held(provider.held(clean), secret) is None

    await written(transport, workspace, "echo dG9rLTAxMjM0NTY3ODlhYmNkZWY= > ../elsewhere")
    leaked = await provider.snapshot(workspace)

    assert await secret_held(provider.held(leaked), secret) == "SERVICE_TOKEN"
    assert b"tok-" not in leaked


async def test_the_null_machines_refuse_every_vm_workspace(tmp_path: Path) -> None:
    provider = AgentWorkspaceVmImpl(VmsNullImpl(), "image", PREFIX, TIMEOUT)
    with pytest.raises(IsolationRefused, match="runs no VMs"):
        await provider.prepare(new_id(), new_id(), vm())


class FakeLima:
    """`limactl`, faked: it answers the instances it is set up with, and
    keeps every command it is given."""

    def __init__(self) -> None:
        self.instances: list[dict[str, object]] = []
        self.calls: list[tuple[str, ...]] = []

    async def __call__(self, *args: str, bound: timedelta, stdin: bytes | None = None) -> VmReply:
        self.calls.append(args)
        if args[-2:] == ("list", "--json"):
            lines = "\n".join(json.dumps(instance) for instance in self.instances)
            return VmReply(0, lines.encode(), b"")
        return VmReply(0, b"", b"")


def lima(fake: FakeLima, hypervisor_says: str | None = None) -> VmsLimaImpl:
    vms = VmsLimaImpl(TIMEOUT, TIMEOUT, encrypted=True, probe_hypervisor=lambda: hypervisor_says)
    vms._limactl = fake  # type: ignore[method-assign]
    return vms


async def test_lima_with_no_hypervisor_refuses_before_any_machine_command() -> None:
    fake = FakeLima()
    provider = AgentWorkspaceVmImpl(
        lima(fake, "this Mac offers no hypervisor (kern.hv_support)"), "template:x", PREFIX, TIMEOUT
    )

    with pytest.raises(IsolationRefused, match=r"no hypervisor \(kern.hv_support\)"):
        await provider.prepare(new_id(), new_id(), vm())

    assert fake.calls == [("--version",)], "only the probe ran"


def longest_prefix() -> str:
    """The longest VM prefix the setting allows."""
    field = InfraSettings.model_fields["vm_prefix"]
    (bound,) = [rule.max_length for rule in field.metadata if isinstance(rule, MaxLen)]
    prefix = "a" * (bound - 1) + "-"
    assert InfraSettings(vm_prefix=prefix).vm_prefix == prefix
    return prefix


async def test_lima_names_fit_its_socket_path_or_its_probe_refuses_before_any_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lima keeps a socket in each VM's folder, and refuses a VM
    whose path to it reaches 104 characters on macOS. Under the longest
    prefix the setting allows, a Lima home that fits keeps every VM's,
    snapshot's, and copy's path under that bound. A home of 60 characters,
    a long macOS short name's, does not fit: the probe refuses with the
    reason before any command, and no VM starts."""
    prefix = longest_prefix()
    for depth in (18, 30, 60):
        home = Path("/" + "h" * (depth - 1))
        monkeypatch.setenv("LIMA_HOME", str(home))
        monkeypatch.setattr("acme.infra.vms.lima.socket_bound", lambda: 104)
        fake = FakeLima()
        vms = lima(fake)
        provider = AgentWorkspaceVmImpl(vms, "template:x", prefix, TIMEOUT)
        refused = name_refused(longest_name(prefix), home, 104)
        if refused is None:
            workspace = await provider.prepare(new_id(), new_id(), vm())
            fake.instances = [{"name": workspace.location, "status": "Running", "dir": ""}]
            unused = provider._unused  # pyright: ignore[reportPrivateUsage]
            named = [workspace.location, *[await unused(workspace.id) for _ in range(20)]]
            for name in named:
                assert len(str(home / name / SOCKET)) < 104, (depth, name)
            continue
        assert depth == 60, f"a home of {depth} characters fits"
        with pytest.raises(IsolationRefused, match="LIMA_HOME") as caught:
            await provider.prepare(new_id(), new_id(), vm())
        assert str(home) in caught.value.message
        assert fake.calls == [], "the probe refused before any command"


async def test_lima_refuses_no_egress_and_a_process_limit_before_it_is_called() -> None:
    fake = FakeLima()
    provider = AgentWorkspaceVmImpl(lima(fake), "template:x", PREFIX, TIMEOUT)
    for refused in (vm(NONE), vm(processes=10)):
        with pytest.raises(IsolationRefused):
            await provider.prepare(new_id(), new_id(), refused)
    assert fake.calls == []
    with pytest.raises(InfraValidationFailed, match="cannot be closed to egress"):
        await lima(fake).launch("m", VmSpec(image="template:x", egress_open=False))


async def test_a_lima_machine_is_made_from_a_template_that_holds_nothing_of_this_host() -> None:
    """No mount, no port forwarded to the host, no proxy variable, no key of
    the host's and no agent: Docker alone is provisioned inside."""
    made = template(VmSpec(image="template:_images/ubuntu-lts", cpus=2, egress_open=True))

    assert made["plain"] is True and made["mounts"] == [] and made["portForwards"] == []
    assert made["propagateProxyEnv"] is False
    assert made["ssh"] == {"loadDotSSHPubKeys": False, "forwardAgent": False, "forwardX11": False}
    assert made["base"] == ["template:_images/ubuntu-lts"] and made["cpus"] == 2
    for unsafe in ("template:x\nmounts: [{location: '~'}]", "ubuntu lts", "file://~"):
        with pytest.raises(InfraValidationFailed):
            template(VmSpec(image=unsafe, egress_open=True))


def test_the_lima_command_line_carries_nothing_of_the_engines_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENGINE_CREDENTIAL, "postgresql://engine:not-for-tools@127.0.0.1/acme")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "not-for-tools")
    vms = VmsLimaImpl(TIMEOUT, TIMEOUT, encrypted=False)

    assert set(lima_environment()) <= {"PATH", "HOME", "LIMA_HOME", "XDG_CACHE_HOME"} | {
        "XDG_CONFIG_HOME"
    }
    assert dict(vms.channel("m").env) == lima_environment()
    assert vms.channel("m").argv[:3] == ("limactl", "--tty=false", "shell")


async def test_a_lima_launch_that_fails_part_way_leaves_no_machine() -> None:
    fake = FakeLima()

    async def fails_at_start(*args: str, bound: timedelta, stdin: bytes | None = None) -> VmReply:
        if "start" in args:
            fake.calls.append(args)
            return VmReply(1, b"", b"boot failed")
        return await fake(*args, bound=bound, stdin=stdin)

    vms = lima(fake)
    vms._limactl = fails_at_start  # type: ignore[method-assign]
    with pytest.raises(Exception, match="boot failed"):
        await vms.launch("acme-test-m", VmSpec(image="template:x", egress_open=True))
    assert ("--tty=false", "delete", "--force", "acme-test-m") in fake.calls


# On Lima, where this host runs it.


def lima_runs() -> bool:
    return shutil.which("limactl") is not None and hypervisor() is None


def lima_prefix() -> str:
    return os.environ.get("ACME_VM_PREFIX", PREFIX)


def on_lima() -> tuple[AgentWorkspaceVmImpl, VmsLimaImpl]:
    vms = VmsLimaImpl(TIMEOUT * 5, timedelta(minutes=15), encrypted=True)
    image = os.environ.get("ACME_VM_IMAGE", "template:_images/ubuntu-lts")
    return AgentWorkspaceVmImpl(vms, image, lima_prefix(), TIMEOUT * 5), vms


async def purged(provider: AgentWorkspaceProviderInterface, workspace: AgentWorkspace) -> None:
    await provider.purge(workspace.org_id, workspace.id)


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.skipif(not lima_runs(), reason="needs Lima and a hypervisor")
async def test_a_vm_runs_docker_and_its_directory_comes_back_on_a_new_vm(tmp_path: Path) -> None:
    """Docker answers inside. A file written, one written as root, and an
    image built, the directory packed and read out, the VM destroyed, and
    the workspace restored from the parts on a new VM: the files' hashes
    and owners are the same, and the image, made outside the directory, is
    the base's again: gone."""
    provider, vms = on_lima()
    org = new_id()
    transport = transport_over(vms, tmp_path, org)
    workspace = await provider.prepare(org, new_id(), vm(durability=Durability.SNAPSHOT))
    try:
        assert (await written(transport, workspace, "docker info >/dev/null"))[0] == 0
        build = (
            "head -c 4096 /dev/urandom > state.bin && mkdir -p ctx && cp state.bin ctx/ && "
            "sudo -n sh -c 'echo root > root.txt' && "
            "printf 'FROM scratch\\nCOPY state.bin /\\n' > ctx/Dockerfile && "
            "docker build -q -t acme-test-kept ctx >/dev/null && "
            "sha256sum state.bin root.txt && stat -c %U root.txt"
        )
        code, before = await written(transport, workspace, build)
        assert code == 0, before

        pack = await provider.pack(workspace, BOUND)
        parts = await read_out(provider, pack, 1 << 20)
        assert sum(map(len, parts)) == pack.size
        await vms.destroy(workspace.location)
        assert await vms.state(workspace.location) is VmState.ABSENT

        restored = await provider.restore(org, workspace.id, vm(), streamed(parts))
        check = "sha256sum state.bin root.txt && stat -c %U root.txt"
        assert await written(transport, restored, check) == (0, before)
        image = "docker image inspect acme-test-kept >/dev/null 2>&1; echo $?"
        assert await written(transport, restored, image) == (0, "1\n"), "the base's again"
    finally:
        await purged(provider, workspace)
    assert await vms.names(workspace.location) == []


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.skipif(not lima_runs(), reason="needs Lima and a hypervisor")
async def test_nothing_of_the_engines_environment_reaches_the_guest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The engine's credentials are in the process that starts the VM
    and runs its commands; none of them, by name or by value, is in a
    command's environment, the guest's, or what the guest was given at
    boot."""
    values = {
        ENGINE_CREDENTIAL: "postgresql://engine:not-for-tools-739@127.0.0.1/acme",
        "AWS_SECRET_ACCESS_KEY": "aws-not-for-tools-739",
        "HTTPS_PROXY": "http://proxy-not-for-tools-739.test:3128",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    provider, vms = on_lima()
    org = new_id()
    transport = transport_over(vms, tmp_path, org)
    workspace = await provider.prepare(org, new_id(), vm())
    try:
        look = (
            "env; cat /etc/environment; sudo -n cat /proc/1/environ | tr '\\0' '\\n'; "
            "sudo -n grep -rah 'not-for-tools' /mnt/lima-cidata /etc /home 2>/dev/null; true"
        )
        code, seen = await written(transport, workspace, look)
        assert code == 0, seen
        assert "PATH=" in seen, "the environment was read"
        for name, value in values.items():
            assert value not in seen and f"{name}=" not in seen, name
    finally:
        await purged(provider, workspace)


@pytest.mark.integration
@pytest.mark.skipif(not lima_runs(), reason="needs Lima and a hypervisor")
async def test_lima_refuses_egress_it_cannot_close_before_a_machine_starts() -> None:
    provider, vms = on_lima()
    agent_workspace_id = new_id()

    with pytest.raises(IsolationRefused, match="egress to none"):
        await provider.prepare(new_id(), agent_workspace_id, vm(NONE))

    assert await vms.names(vm_name(lima_prefix(), agent_workspace_id)) == []
