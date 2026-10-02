"""The transport's twin runs no process and holds to what the real ones do:
it fences a stale epoch, injects and redacts a secret, records how each
command ended, and times a command out at its deadline."""

import asyncio
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path

import pytest

from acme.infra.base import new_id, utcnow
from acme.infra.secrets.local import SecretsLocalImpl
from acme.infra.transports import CommandSpec, SecretUse, SecretVia, StaleCommand
from acme.infra.transports.broker import BrokerTwinImpl
from acme.infra.transports.redaction import marker
from acme.infra.transports.twin import TransportTwinImpl, TwinReply
from acme.infra.workspaces import EgressMode, EgressPolicy, IsolationMode, IsolationSpec
from acme.infra.workspaces.twin import WorkspaceTwinImpl

TOKEN = SecretUse(name="api_token", via=SecretVia.INJECTED, env="API_TOKEN")


def command(*argv: str, epoch: int = 1, seconds: float = 5, **fields: object) -> CommandSpec:
    return CommandSpec.model_validate(
        {
            "argv": argv,
            "key": new_id(),
            "epoch": epoch,
            "deadline": utcnow() + timedelta(seconds=seconds),
            **fields,
        }
    )


async def test_the_twin_fences_injects_redacts_and_records(tmp_path: Path) -> None:
    org = new_id()
    secrets = SecretsLocalImpl(None, {f"{org.hex}_api_token".upper(): "s3cret-value-123"})

    async def echo(sent: CommandSpec, env: Mapping[str, str]) -> TwinReply:
        return TwinReply(exit_code=0, stdout=f"token={env.get('API_TOKEN')}")

    transport = TransportTwinImpl(secrets, BrokerTwinImpl(), echo)
    spec = IsolationSpec(mode=IsolationMode.TWIN, egress=EgressPolicy(mode=EgressMode.NONE))
    workspace = await WorkspaceTwinImpl().prepare(org, new_id(), spec)
    streamed: list[str] = []

    async def sink(stream: str, text: str) -> None:
        streamed.append(text)

    sent = command("call", epoch=2, secrets=(TOKEN,))
    result = await transport.run(workspace, sent, sink)
    assert result.stdout == f"token={marker('api_token')}" and streamed == [result.stdout]
    assert await transport.outcome(workspace, sent.key, epoch=2) == result
    with pytest.raises(StaleCommand):
        await transport.run(workspace, command("call", epoch=1))
    with pytest.raises(StaleCommand):
        await transport.outcome(workspace, sent.key, epoch=1)


async def test_the_twin_times_a_command_out_at_its_deadline(tmp_path: Path) -> None:
    async def slow(sent: CommandSpec, env: Mapping[str, str]) -> TwinReply:
        await asyncio.sleep(10)
        return TwinReply()

    transport = TransportTwinImpl(SecretsLocalImpl(None), BrokerTwinImpl(), slow)
    spec = IsolationSpec(mode=IsolationMode.TWIN, egress=EgressPolicy(mode=EgressMode.NONE))
    workspace = await WorkspaceTwinImpl().prepare(new_id(), new_id(), spec)
    result = await transport.run(workspace, command("slow", seconds=0.2))
    assert result.timed_out and result.exit_code is None
