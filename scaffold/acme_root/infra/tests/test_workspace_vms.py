"""A VM workspace: a machine per workspace on the machines it is given, Docker
inside it (ADR 1029). Over the machines' twin, a machine is a folder of
this host, so the provider and its transport run whole in the unit suite
(the transport's contract is in `test_transports`): the provider prepares,
releases, snapshots, restores, keeps a copy, and purges. A spec the machines
cannot hold is refused before a machine starts: one whose probe finds no
hypervisor, an egress they cannot close, a limit they cannot enforce, and a
snapshot their store cannot keep encrypted. A snapshot names the disk, never
holds it, and a restore holds the disk to its digest before anything of the
workspace goes.

Over Lima, where this host runs it, a VM workspace runs Docker, and a
snapshot keeps its whole disk, the images Docker built included: a restore
answers the same file and the same image. Nothing of the engine's
environment reaches the guest. These cases are integration cases, skipped,
with the reason, where Lima or a hypervisor is missing; every machine they
make is named under `ACME_MACHINE_PREFIX` and destroyed at their end."""

import json
import os
import shutil
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from annotated_types import MaxLen

from acme.infra.base import new_id, utcnow
from acme.infra.exceptions import InfraValidationFailed
from acme.infra.impl.settings import InfraSettings
from acme.infra.machines import MachineReply, MachineSpec, MachineState
from acme.infra.machines.lima import (
    SOCKET,
    MachinesLimaImpl,
    hypervisor,
    lima_environment,
    name_refused,
    template,
)
from acme.infra.machines.twin import MachinesNullImpl, MachinesTwinImpl
from acme.infra.secrets.local import SecretsLocalImpl
from acme.infra.transports import CommandSpec, TransportInterface, secret_held
from acme.infra.transports.broker import BrokerTwinImpl
from acme.infra.transports.twin import RecordSealTwin
from acme.infra.transports.vm import TransportVmImpl
from acme.infra.workspaces import (
    Durability,
    EgressMode,
    EgressPolicy,
    IsolationMode,
    IsolationRefused,
    IsolationSpec,
    ResourceLimits,
    SnapshotRefused,
    Workspace,
    WorkspaceBase,
    WorkspaceLost,
    WorkspaceProviderInterface,
)
from acme.infra.workspaces.vm import WorkspaceVmImpl, longest_name, machine_name

PREFIX = "acme-test-"
ENGINE_CREDENTIAL = "ACME_DATABASE_URL"
SEAL = RecordSealTwin().seal
OPEN = EgressPolicy(mode=EgressMode.OPEN)
NONE = EgressPolicy(mode=EgressMode.NONE)
TIMEOUT = timedelta(seconds=60)


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


def over_twin(tmp_path: Path, **machines: object) -> tuple[WorkspaceVmImpl, MachinesTwinImpl]:
    twin = MachinesTwinImpl(tmp_path / "machines", **machines)  # type: ignore[arg-type]
    return WorkspaceVmImpl(twin, "twin-image", PREFIX, TIMEOUT), twin


def transport_over(
    machines: MachinesTwinImpl | MachinesLimaImpl, tmp_path: Path, org: UUID
) -> TransportVmImpl:
    secrets = SecretsLocalImpl(None, {})
    return TransportVmImpl(tmp_path / "records", secrets, BrokerTwinImpl(), machines, TIMEOUT)


def command(*argv: str, seconds: float = 600) -> CommandSpec:
    return CommandSpec(
        argv=argv, key=new_id(), epoch=1, deadline=utcnow() + timedelta(seconds=seconds)
    )


async def written(
    transport: TransportInterface, workspace: Workspace, script: str
) -> tuple[int | None, str]:
    ran = await transport.run(workspace, command("sh", "-c", script), seal=SEAL)
    return ran.exit_code, ran.stdout + ran.stderr


async def test_a_vm_workspace_is_snapshotted_restored_and_purged_over_the_twin(
    tmp_path: Path,
) -> None:
    """Its snapshot names a disk the machines keep, by a name and a digest, in
    a few hundred bytes. The machine keeps running. A restore replaces the
    workspace whole from that disk; a purge takes the machine and every
    snapshot kept under it."""
    provider, twin = over_twin(tmp_path)
    org, workspace_id = new_id(), new_id()
    transport = transport_over(twin, tmp_path, org)
    workspace = await provider.prepare(org, workspace_id, vm(durability=Durability.SNAPSHOT))
    assert await written(transport, workspace, "echo kept > kept.txt") == (0, "")

    snapshot = await provider.snapshot(workspace)

    named = json.loads(snapshot)
    assert named["format"] == "acme.vm-snapshot/1" and named["org"] == str(org)
    assert named["digest"].startswith("sha256:") and len(snapshot) < 512, "a name, not a disk"
    assert await twin.state(workspace.location) is MachineState.RUNNING, "it keeps its instance"
    assert await written(transport, workspace, "echo later > kept.txt; touch new.txt") == (0, "")

    restored = await provider.prepare(org, workspace_id, vm(), snapshot=snapshot)
    assert await written(transport, restored, "cat kept.txt; ls") == (0, "kept\nkept.txt\n")

    await provider.purge(org, workspace_id)
    assert await twin.names(machine_name(PREFIX, workspace_id)) == [], "the machine and its disks"


async def test_a_restore_holds_the_disk_to_its_digest_before_the_workspace_goes(
    tmp_path: Path,
) -> None:
    """A disk altered, or gone, loses the workspace with the reason, and the
    machine it would have replaced stands as it was. So does an archive of
    another tenant's, or of another provider's."""
    provider, twin = over_twin(tmp_path)
    org, workspace_id = new_id(), new_id()
    workspace = await provider.prepare(org, workspace_id, vm())
    snapshot = await provider.snapshot(workspace)
    disk = twin.machines[json.loads(snapshot)["name"]].folder
    (disk / "workspace" / "planted.txt").write_text("not the commands'")

    with pytest.raises(WorkspaceLost, match="does not match its digest"):
        await provider.prepare(org, workspace_id, vm(), snapshot=snapshot)
    with pytest.raises(WorkspaceLost, match="another tenant"):
        await provider.prepare(new_id(), workspace_id, vm(), snapshot=snapshot)
    with pytest.raises(WorkspaceLost, match="no vm's"):
        await provider.prepare(org, workspace_id, vm(), snapshot=b"a container's tar")
    shutil.rmtree(disk)
    twin.machines.pop(json.loads(snapshot)["name"])
    with pytest.raises(WorkspaceLost, match="gone"):
        await provider.prepare(org, workspace_id, vm(), snapshot=snapshot)

    assert await twin.state(workspace.location) is MachineState.RUNNING, "nothing of it went"


async def test_a_copy_kept_under_another_workspace_outlives_the_purge_of_its_source(
    tmp_path: Path,
) -> None:
    """A fork's copy, or a base, is kept under its own workspace: the source's
    purge leaves it, a copy asked again is the one made, and the purge of
    its own workspace takes it."""
    provider, twin = over_twin(tmp_path)
    org, parent, child = new_id(), new_id(), new_id()
    transport = transport_over(twin, tmp_path, org)
    workspace = await provider.prepare(org, parent, vm())
    await written(transport, workspace, "echo parent > state.txt")
    snapshot = await provider.snapshot(workspace)

    copy = await provider.keep(snapshot, org, child)
    assert await provider.keep(snapshot, org, child) == copy, "made once"
    await provider.purge(org, parent)
    forked = await provider.prepare(org, child, vm(), snapshot=copy)
    assert await written(transport, forked, "cat state.txt") == (0, "parent\n")

    await provider.purge(org, child)
    assert twin.machines == {}


async def test_a_machine_found_again_keeps_its_disk_and_a_new_one_starts_on_the_base(
    tmp_path: Path,
) -> None:
    provider, twin = over_twin(tmp_path)
    org = new_id()
    transport = transport_over(twin, tmp_path, org)
    setup = vm(durability=Durability.SNAPSHOT).model_copy(
        update={"base": WorkspaceBase(image="twin-image")}
    )
    builder = await provider.prepare(org, new_id(), setup)
    await written(transport, builder, "echo from-the-base > base.txt")
    base = await provider.keep(await provider.snapshot(builder), org, new_id())
    spec = vm().model_copy(
        update={"base": WorkspaceBase(image="twin-image", setup=("echo set up",))}
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
    """Machines that cannot close a guest's egress refuse a workspace that must
    reach nothing, and a base whose setup must; an allowlist, a process
    limit, and part of a cpu are refused by every machine. None of it starts
    a machine. Machines that can close egress meet it."""
    provider, twin = over_twin(tmp_path, closes_egress=False)
    closed_setup = vm().model_copy(
        update={"base": WorkspaceBase(image="twin-image", setup=("true",), egress=NONE)}
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
    assert twin.calls == [], "no machine was asked for"

    closing, closes = over_twin(tmp_path / "closing", closes_egress=True)
    await closing.prepare(new_id(), new_id(), vm(NONE, cpus=2, memory_mb=2048))
    (machine,) = closes.machines.values()
    assert machine.spec == MachineSpec(
        image="twin-image", cpus=2, memory_mb=2048, egress_open=False
    )


async def test_a_store_not_encrypted_at_rest_keeps_no_snapshot(tmp_path: Path) -> None:
    """The store stands for the session key's seal: without it, a spec that
    asks for a snapshot, or a base, is refused before a machine starts, and
    a snapshot is refused before anything stops."""
    provider, twin = over_twin(tmp_path, encrypted=False)
    based = vm().model_copy(update={"base": WorkspaceBase(image="twin-image", setup=("true",))})
    for refused in (vm(durability=Durability.SNAPSHOT), based):
        with pytest.raises(IsolationRefused, match="not encrypted at rest"):
            await provider.prepare(new_id(), new_id(), refused, base=b"{}")
    assert twin.calls == []

    workspace = await provider.prepare(new_id(), new_id(), vm())
    with pytest.raises(SnapshotRefused, match="not encrypted at rest"):
        await provider.snapshot(workspace)
    assert ("stop", workspace.location) not in twin.calls


async def test_a_released_workspace_holds_no_instance_and_refuses_a_snapshot(
    tmp_path: Path,
) -> None:
    provider, twin = over_twin(tmp_path)
    workspace = await provider.prepare(new_id(), new_id(), vm())
    await provider.release(workspace)

    assert await twin.state(workspace.location) is MachineState.STOPPED
    with pytest.raises(SnapshotRefused, match="holds no instance"):
        await provider.snapshot(workspace)


async def test_a_disks_secret_value_is_found_by_the_scan_of_what_the_snapshot_holds(
    tmp_path: Path,
) -> None:
    """The archive names the disk; what it holds is read from the disk, so a
    secret's value a command wrote anywhere on it, encoded or raw, is found
    by the scan every snapshot takes before it is kept."""
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
    provider = WorkspaceVmImpl(MachinesNullImpl(), "image", PREFIX, TIMEOUT)
    with pytest.raises(IsolationRefused, match="runs no machines"):
        await provider.prepare(new_id(), new_id(), vm())


class FakeLima:
    """`limactl`, faked: it answers the instances it is set up with, and
    keeps every command it is given."""

    def __init__(self) -> None:
        self.instances: list[dict[str, object]] = []
        self.calls: list[tuple[str, ...]] = []

    async def __call__(
        self, *args: str, bound: timedelta, stdin: bytes | None = None
    ) -> MachineReply:
        self.calls.append(args)
        if args[-2:] == ("list", "--json"):
            lines = "\n".join(json.dumps(instance) for instance in self.instances)
            return MachineReply(0, lines.encode(), b"")
        return MachineReply(0, b"", b"")


def lima(fake: FakeLima, hypervisor_says: str | None = None) -> MachinesLimaImpl:
    machines = MachinesLimaImpl(
        TIMEOUT, TIMEOUT, encrypted=True, probe_hypervisor=lambda: hypervisor_says
    )
    machines._limactl = fake  # type: ignore[method-assign]
    return machines


async def test_lima_with_no_hypervisor_refuses_before_any_machine_command() -> None:
    fake = FakeLima()
    provider = WorkspaceVmImpl(
        lima(fake, "this Mac offers no hypervisor (kern.hv_support)"), "template:x", PREFIX, TIMEOUT
    )

    with pytest.raises(IsolationRefused, match=r"no hypervisor \(kern.hv_support\)"):
        await provider.prepare(new_id(), new_id(), vm())

    assert fake.calls == [("--version",)], "only the probe ran"


def longest_prefix() -> str:
    """The longest machine prefix the setting allows."""
    field = InfraSettings.model_fields["machine_prefix"]
    (bound,) = [rule.max_length for rule in field.metadata if isinstance(rule, MaxLen)]
    prefix = "a" * (bound - 1) + "-"
    assert InfraSettings(machine_prefix=prefix).machine_prefix == prefix
    return prefix


async def test_lima_names_fit_its_socket_path_or_its_probe_refuses_before_any_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lima keeps a socket in each machine's folder, and refuses a machine
    whose path to it reaches 104 characters on macOS. Under the longest
    prefix the setting allows, a Lima home that fits keeps every machine's,
    snapshot's, and copy's path under that bound. A home of 60 characters,
    a long macOS short name's, does not fit: the probe refuses with the
    reason before any command, and no machine starts."""
    prefix = longest_prefix()
    for depth in (18, 30, 60):
        home = Path("/" + "h" * (depth - 1))
        monkeypatch.setenv("LIMA_HOME", str(home))
        monkeypatch.setattr("acme.infra.machines.lima.socket_bound", lambda: 104)
        fake = FakeLima()
        machines = lima(fake)
        provider = WorkspaceVmImpl(machines, "template:x", prefix, TIMEOUT)
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
    provider = WorkspaceVmImpl(lima(fake), "template:x", PREFIX, TIMEOUT)
    for refused in (vm(NONE), vm(processes=10)):
        with pytest.raises(IsolationRefused):
            await provider.prepare(new_id(), new_id(), refused)
    assert fake.calls == []
    with pytest.raises(InfraValidationFailed, match="cannot be closed to egress"):
        await lima(fake).launch("m", MachineSpec(image="template:x", egress_open=False))


async def test_a_lima_machine_is_made_from_a_template_that_holds_nothing_of_this_host() -> None:
    """No mount, no port forwarded to the host, no proxy variable, no key of
    the host's and no agent: Docker alone is provisioned inside."""
    made = template(MachineSpec(image="template:_images/ubuntu-lts", cpus=2, egress_open=True))

    assert made["plain"] is True and made["mounts"] == [] and made["portForwards"] == []
    assert made["propagateProxyEnv"] is False
    assert made["ssh"] == {"loadDotSSHPubKeys": False, "forwardAgent": False, "forwardX11": False}
    assert made["base"] == ["template:_images/ubuntu-lts"] and made["cpus"] == 2
    for unsafe in ("template:x\nmounts: [{location: '~'}]", "ubuntu lts", "file://~"):
        with pytest.raises(InfraValidationFailed):
            template(MachineSpec(image=unsafe, egress_open=True))


def test_the_lima_command_line_carries_nothing_of_the_engines_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENGINE_CREDENTIAL, "postgresql://engine:not-for-tools@127.0.0.1/acme")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "not-for-tools")
    machines = MachinesLimaImpl(TIMEOUT, TIMEOUT, encrypted=False)

    assert set(lima_environment()) <= {"PATH", "HOME", "LIMA_HOME", "XDG_CACHE_HOME"} | {
        "XDG_CONFIG_HOME"
    }
    assert dict(machines.channel("m").env) == lima_environment()
    assert machines.channel("m").argv[:3] == ("limactl", "--tty=false", "shell")


async def test_a_lima_launch_that_fails_part_way_leaves_no_machine() -> None:
    fake = FakeLima()

    async def fails_at_start(
        *args: str, bound: timedelta, stdin: bytes | None = None
    ) -> MachineReply:
        if "start" in args:
            fake.calls.append(args)
            return MachineReply(1, b"", b"boot failed")
        return await fake(*args, bound=bound, stdin=stdin)

    machines = lima(fake)
    machines._limactl = fails_at_start  # type: ignore[method-assign]
    with pytest.raises(Exception, match="boot failed"):
        await machines.launch("acme-test-m", MachineSpec(image="template:x", egress_open=True))
    assert ("--tty=false", "delete", "--force", "acme-test-m") in fake.calls


# On Lima, where this host runs it.


def lima_runs() -> bool:
    return shutil.which("limactl") is not None and hypervisor() is None


def lima_prefix() -> str:
    return os.environ.get("ACME_MACHINE_PREFIX", PREFIX)


def on_lima() -> tuple[WorkspaceVmImpl, MachinesLimaImpl]:
    machines = MachinesLimaImpl(TIMEOUT * 5, timedelta(minutes=15), encrypted=True)
    image = os.environ.get("ACME_MACHINE_IMAGE", "template:_images/ubuntu-lts")
    return WorkspaceVmImpl(machines, image, lima_prefix(), TIMEOUT * 5), machines


async def purged(provider: WorkspaceProviderInterface, workspace: Workspace) -> None:
    await provider.purge(workspace.org_id, workspace.id)


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.skipif(not lima_runs(), reason="needs Lima and a hypervisor")
async def test_a_vm_runs_docker_and_its_snapshot_keeps_the_whole_disk(tmp_path: Path) -> None:
    """Docker answers inside. A file written and an image built, snapshotted,
    the machine destroyed, and the workspace prepared from the snapshot: the
    file's hash and the image's id are the same."""
    provider, machines = on_lima()
    org = new_id()
    transport = transport_over(machines, tmp_path, org)
    workspace = await provider.prepare(org, new_id(), vm(durability=Durability.SNAPSHOT))
    try:
        assert (await written(transport, workspace, "docker info >/dev/null"))[0] == 0
        build = (
            "head -c 4096 /dev/urandom > state.bin && mkdir -p ctx && cp state.bin ctx/ && "
            "printf 'FROM scratch\\nCOPY state.bin /\\n' > ctx/Dockerfile && "
            "docker build -q -t acme-test-kept ctx >/dev/null && "
            "sha256sum state.bin && docker image inspect --format '{{.Id}}' acme-test-kept"
        )
        code, before = await written(transport, workspace, build)
        assert code == 0, before

        snapshot = await provider.snapshot(workspace)
        await machines.destroy(workspace.location)
        assert await machines.state(workspace.location) is MachineState.ABSENT

        restored = await provider.prepare(org, workspace.id, vm(), snapshot=snapshot)
        check = "sha256sum state.bin && docker image inspect --format '{{.Id}}' acme-test-kept"
        assert await written(transport, restored, check) == (0, before)
    finally:
        await purged(provider, workspace)
    assert await machines.names(workspace.location) == []


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.skipif(not lima_runs(), reason="needs Lima and a hypervisor")
async def test_nothing_of_the_engines_environment_reaches_the_guest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The engine's credentials are in the process that starts the machine
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
    provider, machines = on_lima()
    org = new_id()
    transport = transport_over(machines, tmp_path, org)
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
    provider, machines = on_lima()
    workspace_id = new_id()

    with pytest.raises(IsolationRefused, match="egress to none"):
        await provider.prepare(new_id(), workspace_id, vm(NONE))

    assert await machines.names(machine_name(lima_prefix(), workspace_id)) == []
