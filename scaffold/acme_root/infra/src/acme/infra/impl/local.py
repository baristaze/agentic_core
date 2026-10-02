"""The all-local infra root: every capability in-process or on disk under
one folder, for tests and the fast gate."""

from pathlib import Path

from acme.infra.buckets import BucketsInterface
from acme.infra.buckets.local import BucketsLocalImpl
from acme.infra.cache import CacheInterface, CacheScope
from acme.infra.cache.memory import CacheMemoryImpl
from acme.infra.queues import QueuesInterface
from acme.infra.queues.memory import QueueMemoryImpl
from acme.infra.root import InfraInterface
from acme.infra.secrets import SecretsInterface
from acme.infra.secrets.local import SecretsLocalImpl
from acme.infra.topics import TopicsInterface
from acme.infra.topics.memory import TopicsMemoryImpl
from acme.infra.transports import CredentialBrokerInterface, TransportInterface
from acme.infra.transports.broker import BrokerTwinImpl
from acme.infra.transports.twin import TransportTwinImpl
from acme.infra.workspaces import WorkspaceProviderInterface
from acme.infra.workspaces.twin import WorkspaceTwinImpl


class InfraLocalImpl(InfraInterface):
    def __init__(self, root: Path) -> None:
        self._root = root
        self._caches: dict[CacheScope, CacheInterface] = {
            scope: CacheMemoryImpl(scope) for scope in CacheScope
        }
        self._buckets = BucketsLocalImpl(root / "buckets")
        self._topics = TopicsMemoryImpl()
        self._queues = QueueMemoryImpl()
        self._secrets = SecretsLocalImpl(root / "secrets.env")
        self._workspaces = WorkspaceTwinImpl()
        self._broker = BrokerTwinImpl()
        self._transport = TransportTwinImpl(self._secrets, self._broker)

    def get_cache(self, scope: CacheScope) -> CacheInterface:
        return self._caches[scope]

    def get_buckets(self) -> BucketsInterface:
        return self._buckets

    def get_topics(self) -> TopicsInterface:
        return self._topics

    def get_queues(self) -> QueuesInterface:
        return self._queues

    def get_secrets(self) -> SecretsInterface:
        return self._secrets

    def get_workspaces(self) -> WorkspaceProviderInterface:
        return self._workspaces

    def get_transport(self) -> TransportInterface:
        return self._transport

    def get_broker(self) -> CredentialBrokerInterface:
        return self._broker

    def describe(self) -> list[str]:
        return [
            *(cache.describe() for cache in self._caches.values()),
            self._topics.describe(),
            self._buckets.describe(),
            self._queues.describe(),
            self._secrets.describe(),
            self._workspaces.describe(),
            self._transport.describe(),
            self._broker.describe(),
        ]

    async def start(self) -> None:
        for capability in (self._topics, self._buckets, self._queues, self._secrets):
            await capability.start()
        for runtime in (self._broker, self._workspaces, self._transport):
            await runtime.start()

    async def close(self) -> None:
        for runtime in (self._transport, self._workspaces, self._broker):
            await runtime.close()
        for cache in self._caches.values():
            await cache.close()
        for capability in (self._secrets, self._queues, self._buckets, self._topics):
            await capability.close()
