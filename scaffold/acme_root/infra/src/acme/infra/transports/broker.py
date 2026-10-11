from uuid import UUID

from acme.infra.transports import CapabilityMissing, CredentialBrokerInterface, SecretUse
from acme.infra.workspaces import Workspace


class BrokerNullImpl(CredentialBrokerInterface):
    """A process with no credential broker: a brokered secret is refused,
    loudly, and never injected instead."""

    async def attach(self, workspace: Workspace, key: UUID, use: SecretUse) -> None:
        raise CapabilityMissing(
            f"no credential broker: {use.name!r} cannot be attached for {use.destination}"
        )

    async def detach(self, workspace: Workspace, key: UUID) -> None:
        return None

    async def detach_all(self, workspace: Workspace) -> None:
        return None

    def describe(self) -> str:
        return "broker=none"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None


class BrokerTwinImpl(CredentialBrokerInterface):
    """The broker's twin: it attaches nothing anywhere and remembers what it
    was asked to attach, per command, and in which workspace, until the
    command or the workspace takes it back."""

    def __init__(self) -> None:
        self.attached: dict[UUID, list[SecretUse]] = {}
        self.detached: list[UUID] = []
        self._workspace_of: dict[UUID, UUID] = {}

    async def attach(self, workspace: Workspace, key: UUID, use: SecretUse) -> None:
        self.attached.setdefault(key, []).append(use)
        self._workspace_of[key] = workspace.id

    async def detach(self, workspace: Workspace, key: UUID) -> None:
        self.attached.pop(key, None)
        self._workspace_of.pop(key, None)
        self.detached.append(key)

    async def detach_all(self, workspace: Workspace) -> None:
        for key in [k for k, held in self._workspace_of.items() if held == workspace.id]:
            await self.detach(workspace, key)

    def describe(self) -> str:
        return "broker=twin"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None
