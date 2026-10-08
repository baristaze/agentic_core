"""The infra root that picks impls from settings and refuses combinations
that are only safe locally."""

from datetime import timedelta

import aioboto3

from acme.infra.breaker import Breaker
from acme.infra.buckets import BucketsInterface
from acme.infra.buckets.local import BucketsLocalImpl
from acme.infra.buckets.s3 import BucketsS3Impl
from acme.infra.cache import CacheInterface, CacheScope
from acme.infra.cache.breaker import CacheBreakerImpl
from acme.infra.cache.memory import CacheMemoryImpl
from acme.infra.cache.valkey import CacheValkeyImpl
from acme.infra.exceptions import InfraException
from acme.infra.flags import FlagsInterface
from acme.infra.flags.defaults import FlagsDefaultsImpl
from acme.infra.flags.launchdarkly import launchdarkly_config, launchdarkly_flags
from acme.infra.flags.memory import FlagsMemoryImpl
from acme.infra.impl.settings import ENVIRONMENTS, InfraSettings
from acme.infra.impl.valkey import ValkeyConnection
from acme.infra.keys import KeyServiceInterface
from acme.infra.keys.kms import KeyServiceKmsImpl
from acme.infra.keys.memory import KeyServiceMemoryImpl, root_key
from acme.infra.outages import OutageSignalInterface
from acme.infra.outages.shared import OutageSignalCacheImpl
from acme.infra.queues import QueuesInterface
from acme.infra.queues.memory import QueueMemoryImpl
from acme.infra.queues.sqs import QueueSqsImpl
from acme.infra.root import InfraInterface
from acme.infra.secrets import SecretsInterface
from acme.infra.secrets.aws import SecretsAwsImpl
from acme.infra.secrets.local import SecretsLocalImpl
from acme.infra.topics import TopicsInterface
from acme.infra.topics.breaker import TopicsBreakerImpl
from acme.infra.topics.memory import TopicsMemoryImpl
from acme.infra.topics.valkey import TopicsValkeyImpl
from acme.infra.transports import CredentialBrokerInterface, TransportInterface
from acme.infra.transports.broker import BrokerNullImpl
from acme.infra.transports.container import TransportContainerImpl
from acme.infra.transports.local import TransportLocalImpl
from acme.infra.transports.twin import TransportNullImpl
from acme.infra.workspaces import WorkspaceProviderInterface
from acme.infra.workspaces.account import WorkspaceAccountImpl
from acme.infra.workspaces.container import WorkspaceContainerImpl
from acme.infra.workspaces.host import WorkspaceHostImpl
from acme.infra.workspaces.twin import WorkspaceNullImpl


class UnsafeConfiguration(InfraException):
    code = "unsafe_configuration"


UNSAFE_IN_CLOUD: tuple[tuple[str, str, str], ...] = (
    ("secrets_backend", "local", "ACME_SECRETS_BACKEND"),
    ("cache_backend", "memory", "ACME_CACHE_BACKEND"),
    ("topics_backend", "memory", "ACME_TOPICS_BACKEND"),
    ("buckets_backend", "local", "ACME_BUCKETS_BACKEND"),
    ("queues_backend", "memory", "ACME_QUEUES_BACKEND"),
    ("keys_backend", "memory", "ACME_KEYS_BACKEND"),
    # A directory on the host confines files only: a tenant's command would
    # reach other tenants' workspaces, the records, and this process's own
    # environment. An account serves one workspace at a time, on a host that
    # grants the switch. A deployed process runs tools in containers, or none.
    ("workspace_backend", "host", "ACME_WORKSPACE_BACKEND"),
    ("workspace_backend", "account", "ACME_WORKSPACE_BACKEND"),
    ("flags_backend", "memory", "ACME_FLAGS_BACKEND"),
)


def refuse_unsafe(settings: InfraSettings) -> None:
    """Each refusal is a one-line check that exits naming the setting. An
    environment name outside the known set is refused first, so a deployed
    process cannot slip past the cloud checks under a misspelt name. A flag
    vendor named without its key is refused in every environment: the
    process would run on no rules where rules were asked for."""
    if not settings.is_known_environment:
        raise UnsafeConfiguration(
            f"ACME_ENVIRONMENT={settings.environment} is not one of "
            f"{', '.join(sorted(ENVIRONMENTS))}"
        )
    if settings.flags_backend == "launchdarkly" and settings.launchdarkly_sdk_key is None:
        raise UnsafeConfiguration(
            "ACME_FLAGS_BACKEND=launchdarkly is refused without ACME_LAUNCHDARKLY_SDK_KEY"
        )
    if not settings.is_cloud_environment:
        return
    for field, unsafe_value, env_name in UNSAFE_IN_CLOUD:
        if getattr(settings, field) == unsafe_value:
            raise UnsafeConfiguration(
                f"{env_name}={unsafe_value} is refused when ACME_ENVIRONMENT={settings.environment}"
            )


class InfraConfiguredImpl(InfraInterface):
    def __init__(self, settings: InfraSettings) -> None:
        refuse_unsafe(settings)
        self._settings = settings
        # One connection, and one breaker in front of it. A breaker stands for
        # a dependency, not for an interface, so every cache scope and the
        # topic publisher share this one: the first of them to pay the timeouts
        # opens it for all of them, instead of each paying its own bound over
        # again before it protects itself.
        self._valkey: ValkeyConnection | None = None
        self._valkey_breaker: Breaker | None = None
        if settings.cache_backend == "valkey" or settings.topics_backend == "valkey":
            self._valkey = ValkeyConnection(
                settings.valkey_url, timedelta(seconds=settings.valkey_timeout_seconds)
            )
            self._valkey_breaker = Breaker(
                "valkey_breaker",
                failures=settings.valkey_breaker_failures,
                cooldown=timedelta(seconds=settings.valkey_breaker_cooldown_seconds),
                slow=timedelta(seconds=settings.valkey_timeout_seconds),
            )
        aws_timeout = timedelta(seconds=settings.aws_timeout_seconds)
        self._aws = aioboto3.Session(
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.aws_region,
        )
        # One cache per scope, built now like every other member (ADR 0007),
        # so the boot line lists each one and get_cache is a lookup.
        self._caches: dict[CacheScope, CacheInterface] = {
            scope: self._build_cache(scope) for scope in CacheScope
        }
        # The outage signal follows the cache: one process's own over the
        # memory cache, which a deployed process refuses, and the fleet's
        # over Valkey, behind the same breaker.
        self._outages: OutageSignalInterface = OutageSignalCacheImpl(
            self._caches[CacheScope.OUTAGE]
        )

        if settings.buckets_backend == "s3":
            self._buckets: BucketsInterface = BucketsS3Impl(
                self._aws,
                endpoint_url=settings.s3_endpoint_url,
                region=settings.aws_region,
                bucket_prefix=settings.s3_bucket_prefix,
                timeout=aws_timeout,
                presign_endpoint_url=settings.s3_presign_endpoint_url,
            )
        else:
            self._buckets = BucketsLocalImpl(settings.buckets_root)

        if settings.topics_backend == "valkey":
            assert self._valkey is not None
            assert self._valkey_breaker is not None
            # The same breaker the caches hold: one Valkey, one dependency.
            self._topics: TopicsInterface = TopicsBreakerImpl(
                TopicsValkeyImpl(self._valkey), self._valkey_breaker
            )
        else:
            # In process, like the memory cache: it cannot time out.
            self._topics = TopicsMemoryImpl()

        if settings.queues_backend == "sqs":
            self._queues: QueuesInterface = QueueSqsImpl(
                self._aws,
                endpoint_url=settings.sqs_endpoint_url,
                region=settings.aws_region,
                queue_prefix=settings.sqs_queue_prefix,
                timeout=aws_timeout,
            )
        else:
            self._queues = QueueMemoryImpl()

        if settings.secrets_backend == "aws":
            self._secrets: SecretsInterface = SecretsAwsImpl(
                self._aws,
                region=settings.aws_region,
                name_prefix=settings.secrets_name_prefix,
                timeout=aws_timeout,
            )
        else:
            self._secrets = SecretsLocalImpl(settings.secrets_file, settings.secret_overrides)

        if settings.keys_backend == "kms":
            self._keys: KeyServiceInterface = KeyServiceKmsImpl(
                self._aws,
                region=settings.aws_region,
                key_id=settings.kms_key_id,
                timeout=aws_timeout,
            )
        else:
            root = settings.keys_root_key
            self._keys = KeyServiceMemoryImpl(
                None if root is None else root_key(root.get_secret_value())
            )

        # No credential broker runs here, so a brokered secret is refused
        # rather than injected.
        self._broker: CredentialBrokerInterface = BrokerNullImpl()
        self._workspaces, self._transport = self._build_runtime(settings)
        self._flags = self._build_flags()

    def _build_runtime(
        self, settings: InfraSettings
    ) -> tuple[WorkspaceProviderInterface, TransportInterface]:
        """The provider and the transport that runs in what it prepares, as a
        pair."""
        records = settings.workspaces_root / ".records"
        broker = self._broker
        if settings.workspace_backend == "host":
            return (
                WorkspaceHostImpl(settings.workspaces_root),
                TransportLocalImpl(records, self._secrets, broker),
            )
        if settings.workspace_backend == "account":
            account = settings.workspace_account
            return (
                WorkspaceAccountImpl(
                    settings.workspaces_root,
                    account,
                    protected_hardlinks=settings.workspace_protected_hardlinks,
                ),
                TransportLocalImpl(records, self._secrets, broker, account=account),
            )
        if settings.workspace_backend == "container":
            timeout = timedelta(seconds=settings.docker_timeout_seconds)
            return (
                WorkspaceContainerImpl(settings.workspace_image, timeout),
                TransportContainerImpl(records, self._secrets, broker, timeout),
            )
        return WorkspaceNullImpl(), TransportNullImpl()

    def _build_flags(self) -> FlagsInterface:
        """Built now and connected at start: the vendor's client blocks while
        it connects, so it is made off the event loop."""
        settings = self._settings
        if settings.flags_backend == "launchdarkly":
            assert settings.launchdarkly_sdk_key is not None  # refused without one
            timeout = timedelta(seconds=settings.flags_timeout_seconds)
            config = launchdarkly_config(settings.launchdarkly_sdk_key.get_secret_value(), timeout)
            return launchdarkly_flags(config, start_wait=timeout)
        if settings.flags_backend == "none":
            return FlagsDefaultsImpl()
        return FlagsMemoryImpl(file=settings.flags_file)

    def _build_cache(self, scope: CacheScope) -> CacheInterface:
        """Only the out-of-process impl is wrapped. The memory impl is a dict
        on this event loop: it cannot time out and cannot be down, so a breaker
        in front of it would count nothing, refuse nothing, and cost every call
        a layer and every boot line a word that says nothing."""
        if self._settings.cache_backend == "valkey":
            assert self._valkey is not None
            assert self._valkey_breaker is not None
            return CacheBreakerImpl(CacheValkeyImpl(self._valkey, scope), self._valkey_breaker)
        return CacheMemoryImpl(scope)

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

    def get_keys(self) -> KeyServiceInterface:
        return self._keys

    def get_outages(self) -> OutageSignalInterface:
        return self._outages

    def get_workspaces(self) -> WorkspaceProviderInterface:
        return self._workspaces

    def get_transport(self) -> TransportInterface:
        return self._transport

    def get_broker(self) -> CredentialBrokerInterface:
        return self._broker

    def get_flags(self) -> FlagsInterface:
        return self._flags

    def describe(self) -> list[str]:
        return [
            *(cache.describe() for cache in self._caches.values()),
            self._topics.describe(),
            self._buckets.describe(),
            self._queues.describe(),
            self._secrets.describe(),
            self._keys.describe(),
            self._outages.describe(),
            self._workspaces.describe(),
            self._transport.describe(),
            self._broker.describe(),
            self._flags.describe(),
        ]

    async def start(self) -> None:
        if self._valkey is not None:
            await self._valkey.start()
        for capability in (self._topics, self._buckets, self._queues, self._secrets, self._keys, self._flags):
            await capability.start()
        await self._outages.start()
        for runtime in (self._broker, self._workspaces, self._transport):
            await runtime.start()

    async def close(self) -> None:
        """Reverse order of start; the caches first and the shared client
        last, once nothing holds it."""
        for runtime in (self._transport, self._workspaces, self._broker):
            await runtime.close()
        await self._outages.close()
        for cache in self._caches.values():
            await cache.close()
        for capability in (self._flags, self._keys, self._secrets, self._queues, self._buckets, self._topics):
            await capability.close()
        if self._valkey is not None:
            await self._valkey.close()
