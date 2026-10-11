"""Workspaces: the place an agent works, a capability, not a domain. A
provider prepares, releases, and purges a workspace to an isolation spec: a
mode, an egress policy, and resource limits. The transport runs commands
there (`acme.infra.transports`).

Isolation is chosen up front and never weakened. A provider meets a spec
whole or refuses it with `IsolationRefused`, before it creates anything, and
never hands back a weaker place in its stead. A released workspace keeps its
files and loses its instance: the next prepare under the same id finds the
files again. A purged one keeps nothing.

A workspace is a cache. A provider that can also snapshots one: everything
its commands could write, as one archive its own `prepare` starts a
workspace from. A spec that asks for a workspace that can be kept so
(`Durability.SNAPSHOT`) is refused by a provider that cannot, before
anything is made, and is never met by a cache in its stead.

A spec may name a base: an image, and the commands that set it up, run once
with the setup's own egress. A base with setup is a snapshot of a workspace
its setup ran in, so only a provider that snapshots can serve one. A
workspace on it starts from that snapshot and runs with its spec's egress,
never the setup's.

What a workspace is rebuilt from may be gone for good, such as the branch a
checkout tracks. A layer that prepares one then raises `WorkspaceLost`, and
the loop parks, loudly, for a person."""

from abc import ABC, abstractmethod
from collections.abc import Collection
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from acme.infra.base import InfraModel
from acme.infra.exceptions import InfraException, InfraValidationFailed

__all__ = [
    "BaseSetupFailed",
    "Durability",
    "EgressMode",
    "EgressPolicy",
    "IsolationMode",
    "IsolationRefused",
    "IsolationSpec",
    "ResourceLimits",
    "SnapshotRefused",
    "Workspace",
    "WorkspaceBase",
    "WorkspaceLost",
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


class Durability(StrEnum):
    CACHE = "cache"  # its files outlive a release, and may be lost
    SNAPSHOT = "snapshot"  # a snapshot can keep it whole: what a provider that cannot refuses


class ResourceLimits(InfraModel):
    """What a workspace may use of its host. None asks for no limit."""

    cpus: float | None = Field(default=None, gt=0)
    memory_mb: int | None = Field(default=None, gt=0)
    processes: int | None = Field(default=None, gt=0)

    def asked(self) -> frozenset[str]:
        return frozenset(name for name, value in self if value is not None)


class WorkspaceBase(InfraModel):
    """What a workspace starts from: an image, and the commands that set it
    up, run in order, in the workspace's directory, with `egress` alone, such
    as a package registry's. A setup runs commands a person declared and no
    model, is given no secret, and reads no session's content, so its
    workspace holds what a system's package manager needs and nothing
    privileged; every workspace a session uses on the base holds no such
    power. A base with no setup is the image alone."""

    image: str = Field(min_length=1)
    setup: tuple[str, ...] = ()
    egress: EgressPolicy = EgressPolicy(mode=EgressMode.NONE)


class IsolationSpec(InfraModel):
    mode: IsolationMode
    egress: EgressPolicy
    limits: ResourceLimits = ResourceLimits()
    durability: Durability = Durability.CACHE
    base: WorkspaceBase | None = None  # None: the provider's own image


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
    never met with something weaker. A refusal that `clears` waits for a
    workspace that may come, and the loop that asked parks on the resource
    and asks again. Any other, such as a spec the provider does not
    support, never clears, and the loop that asked ends `errored`."""

    code = "isolation_refused"

    def __init__(self, message: str | None = None, *, clears: bool = False) -> None:
        super().__init__(message)
        self.clears = clears


class BaseSetupFailed(IsolationRefused):
    """A command of a base's setup ended without success: the workspace on
    the base is refused, named by the command and how it ended, and nothing
    of the build is kept, so the next prepare builds it again."""

    def __init__(self, index: int, command: str, exit_code: int | None) -> None:
        ended = "ran out of time" if exit_code is None else f"exited {exit_code}"
        super().__init__(f"the base's setup command {index + 1}, {command!r}, {ended}")
        self.command = command
        self.exit_code = exit_code


class SnapshotRefused(InfraValidationFailed):
    """A provider cannot snapshot a workspace: its mode cannot be kept whole,
    or the workspace holds no instance to take one from. Refused before
    anything runs, and never answered with less than the whole."""

    code = "snapshot_refused"


class WorkspaceLost(InfraException):
    """What a workspace is rebuilt from is gone, or cannot be brought in, and
    nothing says how, such as a branch deleted under it or one that moved
    on both sides: never rebuilt from something else in its stead, and the
    loop that asked parks, loudly, for a person."""

    http_status = 409
    code = "workspace_lost"


def refusal(
    spec: IsolationSpec,
    *,
    mode: IsolationMode,
    egress: Collection[EgressMode],
    limits: Collection[str],
    unkept: str | None = None,
) -> str | None:
    """Why a provider of `mode`, which enforces the egress modes and the
    limits named, cannot meet `spec`; None when it meets every part.
    `unkept` says why the provider cannot snapshot a workspace, and is None
    when it can: one that cannot also starts none from a base. A base's
    setup is held to the egress modes named, as the workspace is."""
    if spec.mode is not mode:
        return f"a {mode.value} provider cannot prepare a {spec.mode.value} workspace"
    if spec.egress.mode not in egress:
        return f"a {mode.value} workspace cannot hold egress to {spec.egress.mode.value}"
    if unmet := sorted(spec.limits.asked() - set(limits)):
        return f"a {mode.value} workspace cannot enforce {', '.join(unmet)}"
    if spec.durability is Durability.SNAPSHOT and unkept is not None:
        return f"a {mode.value} workspace cannot be snapshotted: {unkept}"
    if spec.base is not None and unkept is not None:
        return f"a {mode.value} workspace cannot start from a base: {unkept}"
    if spec.base is not None and spec.base.egress.mode not in egress:
        setup = spec.base.egress.mode.value
        return f"a {mode.value} workspace's setup cannot hold egress to {setup}"
    return None


class WorkspaceProviderInterface(ABC):
    @abstractmethod
    async def prepare(
        self,
        org_id: UUID,
        workspace_id: UUID,
        spec: IsolationSpec,
        snapshot: bytes | None = None,
        base: bytes | None = None,
        *,
        building: bool = False,
    ) -> Workspace:
        """The workspace under `workspace_id`, prepared to `spec`: made, or
        found again with its files after a release. `IsolationRefused`, with
        nothing created, when this provider cannot meet every part of the
        spec or cannot reach what it would prepare it on; it `clears` only
        when a workspace may come for the spec later.

        With `snapshot`, an archive this provider's `snapshot` made, the
        workspace starts from it: what it held before is replaced whole. A
        provider that cannot snapshot refuses it with `IsolationRefused`;
        one that cannot bring it in, such as an archive of another
        provider's or one whose image is gone, raises `WorkspaceLost`. A
        restore that fails part way leaves no workspace behind, never a
        half-restored one.

        With `base`, the archive this provider's `snapshot` made of a
        workspace once the setup of `spec.base` ran in it, each instance
        this prepare starts starts from it, and the files it holds are the
        workspace's only when it has none yet: a workspace found again keeps
        its own. `snapshot` wins over it. A spec whose base has setup is
        refused without its archive, never started on the bare image. One
        this provider cannot bring in raises `WorkspaceLost`, and nothing of
        the workspace's own files is lost.

        With `building`, the workspace is the one a base's setup runs in,
        on commands a person declared: its instance holds what a system's
        package manager needs, such as changing a file's owner, and nothing
        privileged. Every other workspace, each one a session uses, holds
        no such power. A provider whose instances hold none ignores it."""
        ...

    @abstractmethod
    async def snapshot(self, workspace: Workspace) -> bytes:
        """Everything the workspace's commands could write, as one archive
        `prepare` starts a workspace from, taken while nothing runs in it.
        The workspace keeps its instance. `SnapshotRefused`, before anything
        runs, when this provider cannot snapshot or the workspace holds no
        instance."""
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
