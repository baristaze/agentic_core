"""The runner boots the way every process does: settings, storage, infra,
the integrations (the model providers), and the managers, with the
product's agent kinds and tools. The claim loop holds the container
directly.

The runner holds no purge login: it runs what a model asks for, and the
one login that deletes a history is the maintenance worker's alone."""

import logging

from acme.infra.impl.configured import InfraConfiguredImpl
from acme.infra.root import InfraInterface
from acme.integrations.impl.configured import IntegrationsConfiguredImpl
from acme.integrations.root import IntegrationsInterface
from acme.om.agents.types.kind import AgentKind
from acme.om.root import Managers, build_managers
from acme.om.storage.impl.postgres import StoragePostgresImpl
from acme.om.storage.root import StorageInterface
from acme.om.tools.attachments import AttachmentReaderInterface
from acme.om.tools.tool import ToolInterface
from acme.workers.session_runner.settings import SessionRunnerSettings

log = logging.getLogger(__name__)


class RunnerContainer:
    def __init__(
        self,
        settings: SessionRunnerSettings,
        storage: StorageInterface,
        infra: InfraInterface,
        integrations: IntegrationsInterface,
        managers: Managers,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.infra = infra
        self.integrations = integrations
        self.managers = managers

    @classmethod
    def build(
        cls,
        settings: SessionRunnerSettings,
        *,
        agent_kinds: tuple[AgentKind, ...] = (),
        tool_catalog: tuple[ToolInterface, ...] = (),
        attachment_reader: AttachmentReaderInterface | None = None,
        domain_classes: tuple[str, ...] = (),
    ) -> RunnerContainer:
        """Over the database, the infra, and the providers the settings
        name. `agent_kinds`, `tool_catalog`, `attachment_reader`, and
        `domain_classes` are the product's, as every process that builds the
        managers passes them; None for the reader refuses every read."""
        storage = StoragePostgresImpl(
            settings.role_urls(),
            settings.role_pools(),
            system_urls=settings.system_role_urls(),
        )
        infra = InfraConfiguredImpl(settings)
        integrations = IntegrationsConfiguredImpl(
            settings, settings.environment, settings.is_cloud_environment
        )
        return cls.over(
            settings,
            storage,
            infra,
            integrations,
            agent_kinds=agent_kinds,
            tool_catalog=tool_catalog,
            attachment_reader=attachment_reader,
            domain_classes=domain_classes,
        )

    @classmethod
    def over(
        cls,
        settings: SessionRunnerSettings,
        storage: StorageInterface,
        infra: InfraInterface,
        integrations: IntegrationsInterface,
        *,
        agent_kinds: tuple[AgentKind, ...] = (),
        tool_catalog: tuple[ToolInterface, ...] = (),
        attachment_reader: AttachmentReaderInterface | None = None,
        domain_classes: tuple[str, ...] = (),
    ) -> RunnerContainer:
        """The managers over whichever roots the caller chose."""
        managers = build_managers(
            storage,
            infra,
            integrations=integrations,
            environment=settings.environment,
            agent_kinds=agent_kinds,
            tool_catalog=tool_catalog,
            attachment_reader=attachment_reader,
            domain_classes=domain_classes,
        )
        return cls(settings, storage, infra, integrations, managers)

    async def start(self) -> None:
        await self.infra.start()
        await self.integrations.start()
        log.info(
            "%s %s started with %s",
            self.settings.service_name,
            self.settings.runner_id,
            ", ".join([*self.infra.describe(), *self.integrations.describe()]),
        )

    async def close(self) -> None:
        await self.integrations.close()
        await self.infra.close()
        await self.storage.close()
