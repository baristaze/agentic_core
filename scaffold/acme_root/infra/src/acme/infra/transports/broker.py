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

    def describe(self) -> str:
        return "broker=none"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None


class BrokerTwinImpl(CredentialBrokerInterface):
    """The broker's twin: it attaches nothing anywhere and remembers what it
    was asked to attach, per command, until the command takes it back."""

    def __init__(self) -> None:
        self.attached: dict[UUID, list[SecretUse]] = {}
        self.detached: list[UUID] = []

    async def attach(self, workspace: Workspace, key: UUID, use: SecretUse) -> None:
        self.attached.setdefault(key, []).append(use)

    async def detach(self, workspace: Workspace, key: UUID) -> None:
        self.attached.pop(key, None)
        self.detached.append(key)

    def describe(self) -> str:
        return "broker=twin"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None
