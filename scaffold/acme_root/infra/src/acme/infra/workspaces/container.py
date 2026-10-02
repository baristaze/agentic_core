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


def container_name(workspace_id: UUID) -> str:
    """The container's name, and its volume's: one per workspace."""
    return f"acme-ws-{workspace_id.hex}"


class WorkspaceContainerImpl(WorkspaceProviderInterface):
    """A container per workspace on the local Docker, with its files in a
    volume of its own that outlives the container. It meets the container
    mode with no egress or open egress, and every resource limit. An
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
        running = await docker(
            "inspect", "--format", "{{.State.Running}}", name, bound=self._timeout
        )
        if running.ok and running.stdout.strip() == b"true":
            return workspace
        if running.ok:
            # A container left stopped: its instance is gone, its files are not.
            await docker("rm", "-f", name, bound=self._timeout)
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

    async def purge(self, workspace: Workspace) -> None:
        await self.release(workspace)
        await docker("volume", "rm", "-f", workspace.location, bound=self._timeout)

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
