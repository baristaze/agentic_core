"""A session end to end over the compose stack: the API in this process,
over the database, the cache, and the stores its settings name, and the
session runner as a process of its own, on the scripted model, running
commands in a directory on this host. A person's message goes in through
the API, the runner claims the loop's work and runs it, and the history is
read back through the API.

The runner is killed for real, mid tool call, with a signal: the next
runner takes the loop under a new epoch once the lease runs out, the lost
run can append nothing more, and the call it left open is settled by its
effect.

Needs a migrated stack (`make migrate`); every case starts on empty
tables. The one case that calls a live provider spends money and runs by
hand alone (`make test-live`)."""

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from api_support import seed_request, sign_in_as
from httpx import ASGITransport
from runner_support import KINDS, answers, runs
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from acme.infra.impl.configured import InfraConfiguredImpl
from acme.infra.transports import StaleCommand
from acme.infra.transports.records import RecordBook
from acme.integrations.impl.configured import absent_integrations
from acme.integrations.model_providers.scripted import SCRIPT, Turn
from acme.integrations.model_providers.types import ProviderName
from acme.om.agents.loop_rules import ended_step
from acme.om.base import new_id, utcnow
from acme.om.budgets.types.budget import Budget, BudgetScopeKind, WindowKind
from acme.om.context import TenantContext
from acme.om.exceptions import StaleWriter
from acme.om.steps.types.header import LoopOutcome
from acme.om.storage.migrate import VERSION_TABLE
from acme.om.storage.roles import DatabaseRole
from acme.om.storage.settings import MigrationSettings
from acme.services.api.app import create_app
from acme.services.api.container import AppContainer, postgres_storage
from acme.services.api.settings import ApiSettings

pytestmark = [pytest.mark.integration, pytest.mark.slow]

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
ENTRY = HERE / "e2e_runner.py"

BOOT_SECONDS = 60.0
"""How long a runner process gets to answer /healthz: a whole interpreter's
imports and one container. A bound on a boot that hangs, not a schedule."""

SETTLE_SECONDS = 90.0
"""How long a loop gets to end or park. The longest case waits out a lost
runner's lease (3 seconds here), a sweep (1), the requeue's stagger (5 at
most), and a poll (1), then two model turns."""

LEASE_SECONDS = 3


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@dataclass
class Runner:
    """A runner process this suite started, and its log."""

    process: subprocess.Popen[bytes]
    log: Path
    port: int

    def tail(self) -> str:
        return "\n".join(self.log.read_text(errors="replace").splitlines()[-40:])

    def stop(self) -> None:
        """SIGTERM, which drains its items back to the queue, then SIGKILL
        when it does not stop; by its own pid only."""
        if self.process.poll() is not None:
            return
        self.process.send_signal(signal.SIGTERM)
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=15)


@dataclass
class Stack:
    """The API in this process over the stack, and the runners started for
    the case."""

    container: AppContainer
    client: httpx.AsyncClient
    tmp_path: Path
    workspaces: Path
    runners: list[Runner] = field(default_factory=lambda: [])

    def runner(self, name: str, script: list[Turn] | None = None, **env: str) -> Runner:
        """A runner process over the stack. With `script`, the scripted model
        answers its calls with those turns, in order; with none, the live
        providers the environment names."""
        port = free_port()
        overrides = {
            "ACME_RUNNER_ID": name,
            "ACME_RUNNER_METRICS_PORT": str(port),
            "ACME_RUNNER_LEASE_SECONDS": str(LEASE_SECONDS),
            "ACME_RUNNER_HEARTBEAT_SECONDS": "1",
            "ACME_RUNNER_SWEEP_SECONDS": "1",
            "ACME_RUNNER_POLL_SECONDS": "1",
            "ACME_WORKSPACE_BACKEND": "host",
            "ACME_WORKSPACES_ROOT": str(self.workspaces),
            "ACME_SENTRY_DSN": "off",
            "ACME_OTEL_ENDPOINT": "",
            **env,
        }
        if script is not None:
            path = self.tmp_path / f"{name}.script.json"
            path.write_bytes(SCRIPT.dump_json({ProviderName.ANTHROPIC: script}))
            overrides |= {"ACME_MODEL_PROVIDERS": "scripted", "ACME_MODEL_SCRIPT": str(path)}
        log = self.tmp_path / f"{name}.log"
        with log.open("wb") as out:
            process = subprocess.Popen(
                [sys.executable, str(ENTRY), "serve"],
                cwd=REPO,
                env={**os.environ, **overrides},
                stdout=out,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        runner = Runner(process, log, port)
        self.runners.append(runner)
        deadline = time.monotonic() + BOOT_SECONDS
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail(f"runner {name} exited at boot:\n{runner.tail()}")
            try:
                if httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=1).status_code == 200:
                    return runner
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        pytest.fail(f"runner {name} never answered /healthz:\n{runner.tail()}")


def empty_tables(settings: MigrationSettings) -> None:
    """Every table but the migrations' bookkeeping, emptied under the
    migration login, so a case reads only what it wrote and no runner claims
    another case's work."""

    async def truncate() -> None:
        for url in dict.fromkeys(settings.migration_role_urls().values()):
            engine = create_async_engine(url)
            try:
                async with engine.begin() as connection:
                    schemas = ", ".join(f"'{role.value}'" for role in DatabaseRole)
                    rows = await connection.execute(
                        text(
                            "SELECT table_schema, table_name FROM information_schema.tables"
                            f" WHERE table_schema IN ({schemas}) AND table_type = 'BASE TABLE'"
                            " AND table_name <> :version_table"
                        ),
                        {"version_table": VERSION_TABLE},
                    )
                    tables = [f'{schema}."{name}"' for schema, name in rows]
                    if tables:
                        await connection.execute(
                            text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE")
                        )
            finally:
                await engine.dispose()

    asyncio.run(truncate())


@pytest.fixture
def emptied() -> None:
    settings = MigrationSettings()
    settings.refuse_remote()
    empty_tables(settings)


@pytest.fixture
async def stack(emptied: None, tmp_path: Path) -> AsyncIterator[Stack]:
    # The developer's tracker and exporter are left off: an error a case
    # raises on purpose is no report.
    settings = ApiSettings(dev_sign_in_enabled=True, sentry_dsn=None, otel_endpoint=None)
    settings.refuse_remote()
    container = AppContainer.over(
        settings,
        postgres_storage(settings),
        InfraConfiguredImpl(settings),
        absent_integrations(),
        agent_kinds=KINDS,
    )
    app = create_app(container)
    workspaces = tmp_path / "workspaces"
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://e2e") as client:
            built = Stack(container, client, tmp_path, workspaces)
            try:
                yield built
            finally:
                for runner in built.runners:
                    runner.stop()


@dataclass
class Person:
    ctx: TenantContext
    headers: dict[str, str]


async def owner_of(stack: Stack) -> Person:
    ctx, org = await stack.container.managers.tenancy.bootstrap(
        seed_request(), "Ajax", "ajax", "ann@example.test", "Ann"
    )
    return Person(ctx, await sign_in_as(stack.client, "ann@example.test", org.id))


def created(headers: dict[str, str]) -> dict[str, str]:
    return {**headers, "Idempotency-Key": str(new_id())}


async def started(stack: Stack, person: Person) -> str:
    answered = await stack.client.post(
        "/v1/agent-sessions",
        headers=created(person.headers),
        json={"kind": "assistant", "title": "the dropped object"},
    )
    assert answered.status_code == 201, answered.text
    return answered.json()["id"]


async def say(stack: Stack, person: Person, session_id: str, words: str) -> dict[str, Any]:
    said = await stack.client.post(
        f"/v1/agent-sessions/{session_id}/messages",
        headers=created(person.headers),
        json={"text": words},
    )
    assert said.status_code == 201, said.text
    return said.json()


async def history(stack: Stack, person: Person, session_id: str) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    while True:
        after = steps[-1]["seq"] if steps else 0
        page = await stack.client.get(
            f"/v1/agent-sessions/{session_id}/steps",
            headers=person.headers,
            params={"after_seq": after, "limit": 200},
        )
        assert page.status_code == 200, page.text
        steps.extend(page.json()["items"])
        if not page.json()["has_more"]:
            return steps


async def settled(stack: Stack, person: Person, session_id: str) -> dict[str, Any]:
    """The session once its loop ended or parked."""
    deadline = asyncio.get_running_loop().time() + SETTLE_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        read = await stack.client.get(f"/v1/agent-sessions/{session_id}", headers=person.headers)
        assert read.status_code == 200, read.text
        if read.json()["status"] in ("idle", "parked"):
            return read.json()
        await asyncio.sleep(0.25)
    logs = "\n\n".join(f"{r.log.name}:\n{r.tail()}" for r in stack.runners)
    pytest.fail(f"session {session_id} never settled\n{logs}")


async def until_steps(
    stack: Stack, person: Person, session_id: str, *types: str
) -> list[dict[str, Any]]:
    """The history once it holds a step of each type named."""
    deadline = asyncio.get_running_loop().time() + SETTLE_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        steps = await history(stack, person, session_id)
        if set(types) <= {step["type"] for step in steps}:
            return steps
        await asyncio.sleep(0.2)
    logs = "\n\n".join(f"{r.log.name}:\n{r.tail()}" for r in stack.runners)
    pytest.fail(f"session {session_id} never held {types}\n{logs}")


def of_type(steps: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [step for step in steps if step["type"] == kind]


MODEL_TOOL_MODEL = [
    "message",
    "model_request",
    "model_response",
    "tool_request",
    "tool_response",
    "model_request",
    "model_response",
    "loop_ended",
]


async def test_a_message_through_the_api_is_run_by_the_runner_model_tool_model_to_its_end(
    stack: Stack,
) -> None:
    person = await owner_of(stack)
    stack.runner(
        "runner-one",
        [runs("echo", "the order drops at 0.4 s"), answers("The order drops before its payment.")],
    )
    session_id = await started(stack, person)

    await say(stack, person, session_id, "Why does the checkout drop the order?")
    session = await settled(stack, person, session_id)

    steps = await history(stack, person, session_id)
    assert [step["type"] for step in steps] == MODEL_TOOL_MODEL
    assert [step["seq"] for step in steps] == list(range(1, 9))
    assert session["status"] == "idle" and session["park"] is None
    assert steps[-1]["outcome"] == "succeeded"
    assert steps[3]["tool"] == "run_command" and steps[4]["failure"] is None
    assert "the order drops at 0.4 s" in steps[4]["text"]
    assert steps[-2]["text"] == "The order drops before its payment."


async def test_a_runner_killed_mid_tool_call_is_followed_by_a_new_epoch_that_settles_the_call(
    stack: Stack,
) -> None:
    person = await owner_of(stack)
    lost = stack.runner("runner-lost", [runs("sh", "-c", "echo ran >> marker && exec sleep 15")])
    session_id = await started(stack, person)
    await say(stack, person, session_id, "Note the fix, then tell me.")
    await until_steps(stack, person, session_id, "tool_request")
    for _ in range(int(SETTLE_SECONDS * 10)):
        if list(stack.workspaces.rglob("marker")):
            break
        await asyncio.sleep(0.1)
    (marker,) = stack.workspaces.rglob("marker")
    steps = stack.container.managers.steps
    sid = UUID(session_id)
    first = (await steps.get_cursor(person.ctx, sid)).epoch

    os.kill(lost.process.pid, signal.SIGKILL)
    assert lost.process.wait(timeout=15) == -signal.SIGKILL
    stack.runner(
        "runner-next", [answers("The note may not have finished; I checked before going on.")]
    )
    session = await settled(stack, person, session_id)

    after = await history(stack, person, session_id)
    second = (await steps.get_cursor(person.ctx, sid)).epoch
    assert second > first, "the next run took an epoch above the lost one's"
    assert session["status"] == "idle" and after[-1]["outcome"] == "succeeded"
    (request,) = of_type(after, "tool_request")
    (answer,) = of_type(after, "tool_response")
    assert answer["responds_to"] == request["id"]
    assert answer["failure"] == "interrupted" and "lost" in answer["text"]
    assert marker.read_text() == "ran\n", "an unsafe call is never run again"
    # The lost run, were it alive, could append nothing and run nothing more.
    with pytest.raises(StaleWriter):
        late = ended_step(new_id(), utcnow(), sid, UUID(after[0]["loop_id"]), LoopOutcome.FAILED)
        await steps.append_steps(person.ctx, sid, first, [late])
    (workspace,) = [d for d in (stack.workspaces / ".records").iterdir() if d.is_dir()]
    with pytest.raises(StaleCommand):
        RecordBook(stack.workspaces / ".records").admit(UUID(hex=workspace.name), first)
    assert len(await history(stack, person, session_id)) == len(after)


async def test_a_message_sent_mid_run_is_kept_at_once_and_read_by_the_next_request(
    stack: Stack,
) -> None:
    person = await owner_of(stack)
    stack.runner(
        "runner-steered",
        [runs("sh", "-c", "sleep 4; echo measured"), answers("Noted: the gains stay as they are.")],
    )
    session_id = await started(stack, person)
    await say(stack, person, session_id, "Investigate the drop.")
    await until_steps(stack, person, session_id, "tool_request")

    steering = await say(stack, person, session_id, "Don't touch the controller gains.")
    kept = await history(stack, person, session_id)

    assert [s["id"] for s in kept if s["seq"] == steering["seq"]] == [steering["id"]]
    assert of_type(kept, "tool_response") == [], "it landed while the tool ran"
    await settled(stack, person, session_id)
    steps = await history(stack, person, session_id)
    (answer,) = of_type(steps, "tool_response")
    delivering = of_type(steps, "model_request")[-1]
    assert steering["seq"] < answer["seq"] < delivering["seq"]
    assert steering["id"] in delivering["refs"], "the next request delivered it"
    assert steps[-1]["outcome"] == "succeeded"


LIVE_BUDGET_MICROS = 500_000
"""The most the live case may spend, in millionths of a dollar: its tenant's
budget, which the gate holds every call to before it is made."""


def live_key() -> str:
    key = os.environ.get("ACME_ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        pytest.fail("the live case needs ANTHROPIC_API_KEY or ACME_ANTHROPIC_API_KEY")
    return key


@pytest.fixture
def report() -> Iterator[dict[str, object]]:
    found: dict[str, object] = {}
    yield found
    print(json.dumps(found, default=str))


@pytest.mark.live
async def test_a_real_loop_runs_end_to_end_on_a_live_provider(
    stack: Stack, report: dict[str, object]
) -> None:
    person = await owner_of(stack)
    budget = await stack.container.managers.budgets.create_budget(
        person.ctx,
        Budget(
            id=new_id(),
            created_at=utcnow(),
            updated_at=utcnow(),
            created_by=person.ctx.user_id,
            updated_by=person.ctx.user_id,
            scope_kind=BudgetScopeKind.TENANT,
            scope_key=str(person.ctx.org_id),
            window_kind=WindowKind.LIFE,
            cost_micros=LIVE_BUDGET_MICROS,
        ),
    )
    stack.runner(
        "runner-live", None, ACME_MODEL_PROVIDERS="live", ACME_ANTHROPIC_API_KEY=live_key()
    )
    session_id = await started(stack, person)

    await say(
        stack,
        person,
        session_id,
        "Run `echo 41` in the workspace with run_command, add one to what it prints, "
        "and answer with the number alone.",
    )
    session = await settled(stack, person, session_id)

    steps = await history(stack, person, session_id)
    spend = await stack.container.managers.budgets.get_spend(person.ctx, budget.id)
    report |= {
        "steps": [step["type"] for step in steps],
        "answer": of_type(steps, "model_response")[-1]["text"],
        "spent_usd": spend.spent_cost_micros / 1_000_000,
        "held_usd": spend.held_cost_micros / 1_000_000,
    }
    assert session["status"] == "idle" and steps[-1]["outcome"] == "succeeded", report
    assert "tool_response" in report["steps"]  # pyright: ignore[reportOperatorIssue]
    assert 0 < spend.spent_cost_micros <= LIVE_BUDGET_MICROS and spend.held_cost_micros == 0
