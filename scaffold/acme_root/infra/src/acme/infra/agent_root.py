"""The engine's infra root: the key service its sessions' keys are wrapped by,
the VMs a VM workspace runs on, the agent workspaces, the credential
broker, and the transport that runs commands in what the provider
prepares. Each infra root builds it with one call, names it once where it
describes, starts, and closes its capabilities, and serves its getters
through `AgentInfraRoot`. Its settings are `AgentInfraSettings`, the base
of the guideline's `InfraSettings`."""

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Literal

import aioboto3
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings

from acme.infra.agent_workspaces import AgentWorkspaceProviderInterface
from acme.infra.agent_workspaces.account import AgentWorkspaceAccountImpl
from acme.infra.agent_workspaces.container import AgentWorkspaceContainerImpl
from acme.infra.agent_workspaces.host import AgentWorkspaceHostImpl
from acme.infra.agent_workspaces.twin import AgentWorkspaceNullImpl, AgentWorkspaceTwinImpl
from acme.infra.agent_workspaces.vm import AgentWorkspaceVmImpl
from acme.infra.keys import KeyServiceInterface
from acme.infra.keys.kms import KeyServiceKmsImpl
from acme.infra.keys.memory import KeyServiceMemoryImpl, root_key
from acme.infra.secrets import SecretsInterface
from acme.infra.transports import CredentialBrokerInterface, TransportInterface
from acme.infra.transports.broker import BrokerNullImpl, BrokerTwinImpl
from acme.infra.transports.container import TransportContainerImpl
from acme.infra.transports.local import TransportLocalImpl
from acme.infra.transports.twin import TransportNullImpl, TransportTwinImpl
from acme.infra.transports.vm import TransportVmImpl
from acme.infra.vms import VmsInterface
from acme.infra.vms.lima import VmsLimaImpl
from acme.infra.vms.twin import VmsNullImpl, VmsTwinImpl


class AgentInfraSettings(BaseSettings):
    """The engine's infra settings, read under the guideline's prefix and
    file by `InfraSettings`, which extends this."""

    # The key service the session keys are wrapped by. The memory one holds
    # each tenant's wrapping key in the process, derived from
    # `keys_root_key`: URL-safe base64 of 32 bytes, or none, for a random
    # root whose keys die with the process. KMS wraps under the key
    # `kms_key_id` names, with `{org_id}` in it for a key per tenant.
    keys_backend: Literal["memory", "kms"] = "memory"
    keys_root_key: SecretStr | None = Field(default=None, repr=False)
    kms_key_id: str = "alias/acme-sessions"

    # Where tools run (ADR 1003 for their secrets). `none` prepares no
    # workspace and refuses every command; `host` a directory per workspace
    # under the root, run as processes of this host; `account` the same,
    # each command run as the account named, one workspace at a time
    # (ADR 1021); `container` a container per workspace on the local Docker,
    # from the image; `vm` a VM per workspace, Docker inside it, on
    # `vms_backend` (ADR 1029). A deployed environment refuses `host`
    # and `account`.
    # The root also holds each transport's records of how commands ended,
    # beside the workspaces. `account` refuses a host that lets an account
    # link a file it does not own; where this process cannot read the
    # host's fs.protected_hardlinks, as under a unit with ProcSubset=pid,
    # `agent_workspace_protected_hardlinks` declares it on.
    agent_workspace_backend: Literal["none", "host", "account", "container", "vm"] = "none"
    agent_workspaces_root: Path = Path(".local/workspaces")
    agent_workspace_account: str = "acme-agent"
    agent_workspace_protected_hardlinks: bool = False
    agent_workspace_image: str = "python:3.14-slim"
    # The VMs a `vm` workspace runs on: `lima` on this host's hypervisor
    # through Lima, or `none`, which runs no VM and refuses every VM
    # workspace. A new VM boots `vm_image`, in the backend's terms
    # (a Lima template here), and every VM and snapshot is named under
    # `vm_prefix`. It holds a project's name and `-vm-`, and no more:
    # Lima keeps a socket under each name, the host bounds its path, and a
    # probe refuses a Lima home too deep for the longest name. A VM's
    # snapshot is its disk, kept in the backend's store;
    # `vm_store_encrypted` declares that store encrypted at rest, and
    # without it a VM workspace keeps no snapshot.
    vms_backend: Literal["none", "lima"] = "none"
    vm_image: str = "template:_images/ubuntu-lts"
    vm_prefix: str = Field(default="acme-vm-", pattern=r"^[a-z][a-z0-9-]*-$", max_length=21)
    vm_store_encrypted: bool = False

    # A Docker command that prepares, releases, or reaches into a container
    # workspace; the first prepare may pull the image.
    docker_timeout_seconds: float = 120.0
    # A command a VM runs for a VM workspace, and a VM's start,
    # which may download its image and install Docker in it.
    vm_timeout_seconds: float = Field(default=120.0, gt=0)
    vm_boot_timeout_seconds: float = Field(default=900.0, gt=0)

    @field_validator("keys_root_key")
    @classmethod
    def _root_key_empty_is_none(cls, value: SecretStr | None) -> SecretStr | None:
        """Empty, a random root per process."""
        if value is None or not value.get_secret_value().strip():
            return None
        return value


AGENT_UNSAFE_IN_CLOUD: tuple[tuple[str, str, str], ...] = (
    ("keys_backend", "memory", "ACME_KEYS_BACKEND"),
    # A directory on the host confines files only: a tenant's command would
    # reach other tenants' workspaces, the records, and this process's own
    # environment. An account serves one workspace at a time, on a host that
    # grants the switch. A deployed process runs tools in containers, or none.
    ("agent_workspace_backend", "host", "ACME_AGENT_WORKSPACE_BACKEND"),
    ("agent_workspace_backend", "account", "ACME_AGENT_WORKSPACE_BACKEND"),
    # A Lima VM reaches this host's own loopback through its gateway,
    # and its egress cannot be closed: a deployed process runs its VMs
    # on the cloud's.
    ("vms_backend", "lima", "ACME_VMS_BACKEND"),
)
"""The engine's backends a deployed environment refuses at boot, beside
the guideline's."""


@dataclass(frozen=True)
class AgentInfra:
    """The engine's capabilities, as one infra root chose them."""

    keys: KeyServiceInterface
    vms: VmsInterface
    agent_workspaces: AgentWorkspaceProviderInterface
    broker: CredentialBrokerInterface
    transport: TransportInterface

    @classmethod
    def local(cls, root: Path, secrets: SecretsInterface) -> AgentInfra:
        """Twins in one folder, for tests and the fast gate."""
        broker = BrokerTwinImpl()
        return cls(
            keys=KeyServiceMemoryImpl(),
            vms=VmsTwinImpl(root / "vms"),
            agent_workspaces=AgentWorkspaceTwinImpl(),
            broker=broker,
            transport=TransportTwinImpl(secrets, broker),
        )

    @classmethod
    def configured(
        cls,
        settings: AgentInfraSettings,
        *,
        aws: aioboto3.Session,
        region: str,
        aws_timeout: timedelta,
        secrets: SecretsInterface,
    ) -> AgentInfra:
        """What `settings` selects, built now and connected at start. No
        credential broker runs here, so a brokered secret is refused rather
        than injected."""
        if settings.keys_backend == "kms":
            keys: KeyServiceInterface = KeyServiceKmsImpl(
                aws, region=region, key_id=settings.kms_key_id, timeout=aws_timeout
            )
        else:
            root = settings.keys_root_key
            keys = KeyServiceMemoryImpl(None if root is None else root_key(root.get_secret_value()))
        broker = BrokerNullImpl()
        vms = _vms(settings)
        agent_workspaces, transport = _runtime(settings, vms, secrets, broker)
        return cls(
            keys=keys,
            vms=vms,
            agent_workspaces=agent_workspaces,
            broker=broker,
            transport=transport,
        )

    def describe(self) -> list[str]:
        return [
            self.keys.describe(),
            self.vms.describe(),
            self.agent_workspaces.describe(),
            self.transport.describe(),
            self.broker.describe(),
        ]

    async def start(self) -> None:
        """The key service first, then what runs commands, each below what
        runs on it."""
        for capability in (
            self.keys,
            self.broker,
            self.vms,
            self.agent_workspaces,
            self.transport,
        ):
            await capability.start()

    async def close(self) -> None:
        """Reverse order of start."""
        for capability in (
            self.transport,
            self.agent_workspaces,
            self.vms,
            self.broker,
            self.keys,
        ):
            await capability.close()


def _runtime(
    settings: AgentInfraSettings,
    vms: VmsInterface,
    secrets: SecretsInterface,
    broker: CredentialBrokerInterface,
) -> tuple[AgentWorkspaceProviderInterface, TransportInterface]:
    """The provider and the transport that runs in what it prepares, as a
    pair."""
    records = settings.agent_workspaces_root / ".records"
    if settings.agent_workspace_backend == "host":
        return (
            AgentWorkspaceHostImpl(settings.agent_workspaces_root),
            TransportLocalImpl(records, secrets, broker),
        )
    if settings.agent_workspace_backend == "account":
        account = settings.agent_workspace_account
        return (
            AgentWorkspaceAccountImpl(
                settings.agent_workspaces_root,
                account,
                protected_hardlinks=settings.agent_workspace_protected_hardlinks,
            ),
            TransportLocalImpl(records, secrets, broker, account=account),
        )
    if settings.agent_workspace_backend == "container":
        timeout = timedelta(seconds=settings.docker_timeout_seconds)
        return (
            AgentWorkspaceContainerImpl(settings.agent_workspace_image, timeout),
            TransportContainerImpl(records, secrets, broker, timeout),
        )
    if settings.agent_workspace_backend == "vm":
        timeout = timedelta(seconds=settings.vm_timeout_seconds)
        return (
            AgentWorkspaceVmImpl(vms, settings.vm_image, settings.vm_prefix, timeout),
            TransportVmImpl(records, secrets, broker, vms, timeout),
        )
    return AgentWorkspaceNullImpl(), TransportNullImpl()


def _vms(settings: AgentInfraSettings) -> VmsInterface:
    """The VMs a VM workspace runs on; none runs no VM, and its probe
    refuses every VM workspace."""
    if settings.vms_backend == "lima":
        return VmsLimaImpl(
            timedelta(seconds=settings.vm_timeout_seconds),
            timedelta(seconds=settings.vm_boot_timeout_seconds),
            encrypted=settings.vm_store_encrypted,
        )
    return VmsNullImpl()


class AgentInfraRoot:
    """The getters an infra root serves the engine's capabilities by, over
    the `AgentInfra` it built as `_agent_infra`."""

    _agent_infra: AgentInfra

    def get_keys(self) -> KeyServiceInterface:
        return self._agent_infra.keys

    def get_agent_workspaces(self) -> AgentWorkspaceProviderInterface:
        return self._agent_infra.agent_workspaces

    def get_vms(self) -> VmsInterface:
        """The VMs this root's VM workspaces run on."""
        return self._agent_infra.vms

    def get_transport(self) -> TransportInterface:
        """The transport that runs commands in the workspaces this root's
        provider prepares."""
        return self._agent_infra.transport

    def get_broker(self) -> CredentialBrokerInterface:
        """The broker the transport attaches a brokered secret through."""
        return self._agent_infra.broker
