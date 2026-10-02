"""The secrets of one command, for as long as it runs: the brokered ones
attached outside the workspace, the injected ones resolved by name under the
workspace's tenant, and the redaction that matches them. Nothing here
outlives the command (ADR 1003)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from acme.infra.secrets import SecretsInterface
from acme.infra.transports import CommandSpec, CredentialBrokerInterface, SecretVia
from acme.infra.transports.redaction import Redactor
from acme.infra.workspaces import Workspace

BASE_LANG = "C.UTF-8"


@dataclass(frozen=True)
class Injection:
    env: dict[str, str]  # each injected secret's variable and its value
    redactor: Redactor
    names: tuple[str, ...]  # every secret the command uses, by name


@asynccontextmanager
async def injected(
    secrets: SecretsInterface,
    broker: CredentialBrokerInterface,
    workspace: Workspace,
    command: CommandSpec,
) -> AsyncIterator[Injection]:
    """Attaches the brokered secrets, resolves the injected ones, and takes
    back what was attached when the command ends, however it ends. A secret
    that cannot be attached or resolved refuses the command before it runs:
    a brokered one is never injected in its stead."""
    brokered = [use for use in command.secrets if use.via is SecretVia.BROKERED]
    try:
        for use in brokered:
            await broker.attach(workspace, command.key, use)
        values: dict[str, str] = {}
        env: dict[str, str] = {}
        for use in command.secrets:
            if use.via is SecretVia.INJECTED and use.env is not None:
                values[use.name] = await secrets.get(workspace.org_id, use.name)
                env[use.env] = values[use.name]
        yield Injection(
            env=env,
            redactor=Redactor(values),
            names=tuple(use.name for use in command.secrets),
        )
    finally:
        if brokered:
            await broker.detach(workspace, command.key)
