"""Why a loop ended or parked, as the API returns it: a loop runs here
in-process, over the memory storage and the twin workspace and transport,
on a kind whose workspace starts from a base with setup. A setup command
that fails ends the loop `errored`, and its end step names the command and
its exit code, never what the command wrote. A loop whose base another
prepare is building parks on the resource, and its park says so, on the
step and on the session."""

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from api_support import build_container, seed_request

from acme.infra.transports import CommandSpec
from acme.infra.transports.twin import TransportTwinImpl, TwinReply
from acme.infra.workspaces import (
    EgressMode,
    EgressPolicy,
    IsolationMode,
    IsolationSpec,
    WorkspaceBase,
)
from acme.om.agents.types.kind import AgentKind, DoneRule, TreeLimits
from acme.om.attribution.types.authority import AuthorityMode
from acme.om.context import TenantContext
from acme.services.api.container import AppContainer

SETUP = "pip install acme-tools"
WROTE = "ERROR: tok-0123456789abcdef was refused by the registry"
"""What the failing command writes: output, which never reaches a reader."""

BUILDER = AgentKind(
    name="builder",
    version=1,
    done_rule=DoneRule.ANSWER,
    authority=AuthorityMode.DELEGATED,
    tree=TreeLimits(height=1, count=0),
    isolation=IsolationSpec(
        mode=IsolationMode.TWIN,
        egress=EgressPolicy(mode=EgressMode.NONE),
        base=WorkspaceBase(
            image="python:3.14-slim",
            setup=(SETUP,),
            egress=EgressPolicy(mode=EgressMode.OPEN),
        ),
    ),
)


class Setup:
    """The twin transport's handler: a setup command waits for the gate
    when a case holds one, then exits with `exit_code` and writes `WROTE`."""

    def __init__(self, exit_code: int) -> None:
        self.exit_code = exit_code
        self.gate: asyncio.Event | None = None
        self.started = asyncio.Event()

    async def __call__(self, command: CommandSpec, env: Mapping[str, str]) -> TwinReply:
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        return TwinReply(exit_code=self.exit_code, stderr=WROTE)


@pytest.fixture
def container(tmp_path: Path) -> AppContainer:
    return build_container(tmp_path, agent_kinds=(BUILDER,))


def setup_on(container: AppContainer, exit_code: int) -> Setup:
    transport = container.infra.get_transport()
    assert isinstance(transport, TransportTwinImpl)
    setup = Setup(exit_code)
    transport.handler = setup
    return setup


async def spoken_to(client: httpx.AsyncClient, owner: dict[str, str]) -> str:
    """A builder session, started and spoken to: its loop waits for a run."""
    started = await client.post(
        "/v1/agent-sessions",
        headers={**owner, "Idempotency-Key": str(uuid4())},
        json={"kind": "builder", "title": "the build"},
    )
    assert started.status_code == 201, started.text
    session_id = started.json()["id"]
    said = await client.post(
        f"/v1/agent-sessions/{session_id}/messages",
        headers={**owner, "Idempotency-Key": str(uuid4())},
        json={"text": "Run the tests."},
    )
    assert said.status_code == 201, said.text
    return session_id


async def owner_context(container: AppContainer, owner: dict[str, str]) -> TenantContext:
    token = owner["Authorization"].removeprefix("Bearer ")
    return await container.managers.tenancy.authenticate(seed_request(), token)


async def last_step(
    client: httpx.AsyncClient, owner: dict[str, str], session_id: str
) -> dict[str, Any]:
    read = await client.get(f"/v1/agent-sessions/{session_id}/steps", headers=owner)
    assert read.status_code == 200, read.text
    assert WROTE not in read.text, "what a command wrote never reaches a reader"
    return read.json()["items"][-1]


async def test_a_failed_setup_ends_the_loop_with_its_command_and_exit_code(
    client: httpx.AsyncClient, owner: dict[str, str], container: AppContainer
) -> None:
    setup_on(container, exit_code=2)
    session_id = await spoken_to(client, owner)

    await container.managers.loop.run(await owner_context(container, owner), UUID(session_id))
    ended = await last_step(client, owner, session_id)

    assert (ended["type"], ended["outcome"]) == ("loop_ended", "errored")
    assert ended["cause"] == f"the base's setup command 1, {SETUP!r}, exited 2"


async def test_a_park_on_a_base_being_built_says_so_on_the_step_and_the_session(
    client: httpx.AsyncClient, owner: dict[str, str], container: AppContainer
) -> None:
    setup = setup_on(container, exit_code=0)
    setup.gate = asyncio.Event()
    ctx = await owner_context(container, owner)
    building, waiting = await spoken_to(client, owner), await spoken_to(client, owner)
    first = asyncio.create_task(container.managers.loop.run(ctx, UUID(building)))
    await setup.started.wait()

    await container.managers.loop.run(ctx, UUID(waiting))
    parked = await last_step(client, owner, waiting)
    session = await client.get(f"/v1/agent-sessions/{waiting}", headers=owner)
    setup.gate.set()
    await first

    assert (parked["type"], parked["park"]["reason"]) == ("parked", "resource")
    assert parked["park"]["cause"] == "the workspace base is being built"
    assert session.status_code == 200, session.text
    assert session.json()["park"] == parked["park"], "the session shows the park it waits on"
