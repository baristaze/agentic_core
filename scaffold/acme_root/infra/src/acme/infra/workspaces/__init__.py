"""Workspaces: the place an agent works, a capability, not a domain. A
provider prepares, releases, and purges a workspace to an isolation spec: a
mode, an egress policy, and resource limits. The transport runs commands
there (`acme.infra.transports`).

Isolation is chosen up front and never weakened. A provider meets a spec
whole or refuses it with `IsolationRefused`, before it creates anything, and
never hands back a weaker place in its stead. A released workspace keeps its
files and loses its instance: the next prepare under the same id finds the
files again. A purged one keeps nothing."""

from abc import ABC, abstractmethod
from collections.abc import Collection
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from acme.infra.base import InfraModel
from acme.infra.exceptions import InfraValidationFailed

__all__ = [
    "EgressMode",
    "EgressPolicy",
    "IsolationMode",
    "IsolationRefused",
    "IsolationSpec",
    "ResourceLimits",
    "Workspace",
    "WorkspaceProviderInterface",
    "refusal",
]


class IsolationMode(StrEnum):
    VM = "vm"
    CONTAINER = "container"
    HOST = "host"  # a directory on a host
    ACCOUNT = "account"  # a directory on a host, its commands run as an account of its own
    TWIN = "twin"  # the twin, for tests
    NONE = "none"  # no workspace at all: every transport refuses it


class EgressMode(StrEnum):
    NONE = "none"  # nothing leaves
    ALLOWLIST = "allowlist"  # only the hosts named
    OPEN = "open"  # anywhere


class EgressPolicy(InfraModel):
    mode: EgressMode
    hosts: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _hosts_name_an_allowlist(self) -> Self:
        if (self.mode is EgressMode.ALLOWLIST) != bool(self.hosts):
            raise ValueError("an allowlist names its hosts, and no other egress does")
        return self


class ResourceLimits(InfraModel):
    """What a workspace may use of its host. None asks for no limit."""

    cpus: float | None = Field(default=None, gt=0)
    memory_mb: int | None = Field(default=None, gt=0)
    processes: int | None = Field(default=None, gt=0)

    def asked(self) -> frozenset[str]:
        return frozenset(name for name, value in self if value is not None)


class IsolationSpec(InfraModel):
    mode: IsolationMode
    egress: EgressPolicy
    limits: ResourceLimits = ResourceLimits()


class Workspace(InfraModel):
    """A prepared workspace: its id, its tenant, the spec it was prepared to,
    and where its transport finds it (a directory, a container's name)."""

    id: UUID
    org_id: UUID
    spec: IsolationSpec
    location: str

    @classmethod
    def absent(cls, org_id: UUID, workspace_id: UUID) -> Self:
        """The workspace of a session that has none: every transport refuses
        it, loudly."""
        spec = IsolationSpec(mode=IsolationMode.NONE, egress=EgressPolicy(mode=EgressMode.NONE))
        return cls(id=workspace_id, org_id=org_id, spec=spec, location="")


class IsolationRefused(InfraValidationFailed):
    """A provider cannot meet a spec. Refused before anything is created,
    never met with something weaker."""

    code = "isolation_refused"


def refusal(
    spec: IsolationSpec,
    *,
    mode: IsolationMode,
    egress: Collection[EgressMode],
    limits: Collection[str],
) -> str | None:
    """Why a provider of `mode`, which enforces the egress modes and the
    limits named, cannot meet `spec`; None when it meets every part."""
    if spec.mode is not mode:
        return f"a {mode.value} provider cannot prepare a {spec.mode.value} workspace"
    if spec.egress.mode not in egress:
        return f"a {mode.value} workspace cannot hold egress to {spec.egress.mode.value}"
    if unmet := sorted(spec.limits.asked() - set(limits)):
        return f"a {mode.value} workspace cannot enforce {', '.join(unmet)}"
    return None


class WorkspaceProviderInterface(ABC):
    @abstractmethod
    async def prepare(self, org_id: UUID, workspace_id: UUID, spec: IsolationSpec) -> Workspace:
        """The workspace under `workspace_id`, prepared to `spec`: made, or
        found again with its files after a release. `IsolationRefused`, with
        nothing created, when this provider cannot meet every part of the
        spec or cannot reach what it would prepare it on."""
        ...

    @abstractmethod
    async def release(self, workspace: Workspace) -> None:
        """Lets the instance go and keeps the files. Releasing one that holds
        no instance does nothing."""
        ...

    @abstractmethod
    async def purge(self, org_id: UUID, workspace_id: UUID) -> None:
        """Lets the instance and the files of the workspace under
        `workspace_id` go, found by the ids `prepare` names it by, so a purge
        needs no workspace in hand. Purging one already gone, or never made,
        does nothing; one this provider cannot remove is an error."""
        ...

    @abstractmethod
    def describe(self) -> str: ...

    @abstractmethod
    async def start(self) -> None:
        """Opened by the infra root at boot. An impl that holds no connection
        of its own returns None."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Closed by the infra root at shutdown, in reverse order of start."""
        ...
