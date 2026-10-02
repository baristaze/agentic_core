import hashlib
from datetime import timedelta
from uuid import UUID

from acme.infra.docker import docker
from acme.infra.exceptions import BackendFailed
from acme.infra.workspaces import (
    EgressMode,
    IsolationMode,
    IsolationRefused,
    IsolationSpec,
    Workspace,
    WorkspaceProviderInterface,
    refusal,
)

MOUNT = "/workspace"
"""Where a workspace's files sit inside its container."""

LIMIT_FLAGS = {"cpus": "--cpus", "memory_mb": "--memory", "processes": "--pids-limit"}


SPEC_LABEL = "acme.spec"
"""The label that names the spec a container was started to."""

STARTED_TO = '{{.State.Running}} {{index .Config.Labels "' + SPEC_LABEL + '"}}'
"""What an inspect answers of a container: whether it runs, and the spec it
was started to."""


def container_name(workspace_id: UUID) -> str:
    """The container's name, and its volume's: one per workspace."""
    return f"acme-ws-{workspace_id.hex}"


def spec_print(spec: IsolationSpec) -> str:
    """The fingerprint of a spec, as a container started to it is labelled:
    a running container is reused only under the spec it was started to."""
    return hashlib.sha256(spec.model_dump_json().encode()).hexdigest()


class WorkspaceContainerImpl(WorkspaceProviderInterface):
    """A container per workspace on the local Docker, with its files in a
    volume of its own that outlives the container. It meets the container
    mode with no egress or open egress, and every resource limit. A running
    container is reused only under the spec it was started to; under any
    other, a tightened one included, it is replaced and its files kept. An
    allowlist needs an egress proxy this provider does not run, so it is
    refused; so is every spec when Docker cannot be reached. There is no
    weaker place to fall back to.

    The container drops every capability, takes no new privileges, and
    keeps nothing of the engine's environment: its variables are the
    image's."""

    def __init__(self, image: str, timeout: timedelta) -> None:
        self._image = image
        self._timeout = timeout

    async def prepare(self, org_id: UUID, workspace_id: UUID, spec: IsolationSpec) -> Workspace:
        why = refusal(
            spec,
            mode=IsolationMode.CONTAINER,
            egress={EgressMode.NONE, EgressMode.OPEN},
            limits=LIMIT_FLAGS.keys(),
        )
        if why is not None:
            raise IsolationRefused(why)
        reachable = await docker("version", "--format", "{{.Server.Version}}", bound=self._timeout)
        if not reachable.ok:
            raise IsolationRefused(f"a container workspace needs Docker: {reachable.reason()}")
        name = container_name(workspace_id)
        workspace = Workspace(id=workspace_id, org_id=org_id, spec=spec, location=name)
        running = await docker("inspect", "--format", STARTED_TO, name, bound=self._timeout)
        printed = spec_print(spec)
        if running.ok and running.stdout.split() == [b"true", printed.encode()]:
            return workspace
        if running.ok:
            # A container left stopped, or started to another spec, such as
            # one tightened since: its instance goes, its files stay, and
            # the container that replaces it holds this spec.
            removed = await docker("rm", "-f", name, bound=self._timeout)
            if not removed.ok:
                raise BackendFailed("docker", "rm", removed.reason())
        labels = ("--label", f"acme.workspace={workspace_id}", "--label", f"acme.org={org_id}")
        made = await docker("volume", "create", *labels, name, bound=self._timeout)
        if not made.ok:
            raise BackendFailed("docker", "volume create", made.reason())
        started = await docker(
            "run",
            "--detach",
            "--init",
            "--name",
            name,
            *labels,
            "--label",
            f"{SPEC_LABEL}={printed}",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            *_network(spec),
            *_limits(spec),
            "--volume",
            f"{name}:{MOUNT}",
            "--workdir",
            MOUNT,
            self._image,
            "sleep",
            "infinity",
            bound=self._timeout,
        )
        if not started.ok:
            raise BackendFailed("docker", "run", started.reason())
        return workspace

    async def release(self, workspace: Workspace) -> None:
        await docker("rm", "-f", workspace.location, bound=self._timeout)

    async def purge(self, org_id: UUID, workspace_id: UUID) -> None:
        name = container_name(workspace_id)
        for removal in (("rm", "-f", name), ("volume", "rm", "-f", name)):
            removed = await docker(*removal, bound=self._timeout)
            if not removed.ok:
                raise BackendFailed("docker", " ".join(removal[:-2]), removed.reason())

    def describe(self) -> str:
        return f"workspaces=container({self._image})"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None


def _network(spec: IsolationSpec) -> tuple[str, ...]:
    return ("--network", "none") if spec.egress.mode is EgressMode.NONE else ()


def _limits(spec: IsolationSpec) -> tuple[str, ...]:
    flags: list[str] = []
    limits = spec.limits
    if limits.cpus is not None:
        flags += [LIMIT_FLAGS["cpus"], str(limits.cpus)]
    if limits.memory_mb is not None:
        flags += [LIMIT_FLAGS["memory_mb"], f"{limits.memory_mb}m"]
    if limits.processes is not None:
        flags += [LIMIT_FLAGS["processes"], str(limits.processes)]
    return tuple(flags)
