"""Infrastructure is fronted by a single root so consumers can ask for what
they need. The root has a lifecycle because some capabilities do, and every
capability declares one so the root never asks which."""

from abc import ABC, abstractmethod

from acme.infra.buckets import BucketsInterface
from acme.infra.cache import CacheInterface, CacheScope
from acme.infra.flags import FlagsInterface
from acme.infra.keys import KeyServiceInterface
from acme.infra.machines import MachinesInterface
from acme.infra.outages import OutageSignalInterface
from acme.infra.queues import QueuesInterface
from acme.infra.secrets import SecretsInterface
from acme.infra.topics import TopicsInterface
from acme.infra.transports import CredentialBrokerInterface, TransportInterface
from acme.infra.workspaces import WorkspaceProviderInterface


class InfraInterface(ABC):
    @abstractmethod
    def get_cache(self, scope: CacheScope) -> CacheInterface: ...

    @abstractmethod
    def get_buckets(self) -> BucketsInterface: ...

    @abstractmethod
    def get_topics(self) -> TopicsInterface: ...

    @abstractmethod
    def get_queues(self) -> QueuesInterface: ...

    @abstractmethod
    def get_secrets(self) -> SecretsInterface: ...

    @abstractmethod
    def get_keys(self) -> KeyServiceInterface: ...

    @abstractmethod
    def get_workspaces(self) -> WorkspaceProviderInterface: ...

    @abstractmethod
    def get_machines(self) -> MachinesInterface:
        """The machines this root's VM workspaces run on."""
        ...

    @abstractmethod
    def get_transport(self) -> TransportInterface:
        """The transport that runs commands in the workspaces this root's
        provider prepares."""
        ...

    @abstractmethod
    def get_broker(self) -> CredentialBrokerInterface:
        """The broker the transport attaches a brokered secret through."""
        ...

    @abstractmethod
    def get_flags(self) -> FlagsInterface: ...

    @abstractmethod
    def get_outages(self) -> OutageSignalInterface:
        """The outage signal: on the shared cache where processes share one,
        and the null impl in a process that keeps its cache to itself."""
        ...

    @abstractmethod
    def describe(self) -> list[str]:
        """One line per chosen backend, logged once at boot."""
        ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...
