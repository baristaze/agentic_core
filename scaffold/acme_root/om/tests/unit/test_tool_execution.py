"""A call that runs: through the transport, in its workspace, under its key
and its run's epoch, by the least of three times, with each secret it uses
audited by name before its command runs and redacted from everything it
prints. Its failures carry their class and the advice the model reads; one
worth retrying, of a tool safe to repeat, is run again once first. An
output past its bound keeps its head and its tail, and an input that is not
one JSON object never runs."""

import os
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from contracts.doubles import context
from contracts.factories import make_org
from contracts.tools import (
    HOST_SPEC,
    INJECTED_TOKEN,
    KIND_DEFAULTS,
    TWIN_SPEC,
    Command,
    CommandOutput,
    PushBranch,
    Tools,
    echoing,
    failure_of,
    put_call,
    registry_of,
    result_text,
    tools_over,
    twin_transport,
)

from acme.infra.secrets.local import SecretsLocalImpl
from acme.infra.transports.broker import BrokerTwinImpl
from acme.infra.transports.local import DEFAULT_PATH, TransportLocalImpl
from acme.infra.transports.redaction import forms, marker
from acme.infra.workspaces import (
    EgressMode,
    EgressPolicy,
    IsolationMode,
    IsolationRefused,
    IsolationSpec,
    Workspace,
)
from acme.infra.workspaces.host import WorkspaceHostImpl
from acme.om.base import Platform, new_id, utcnow
from acme.om.context import Role, TenantContext
from acme.om.exceptions import StaleWriter, ToolFailed
from acme.om.steps.types.content import UNPARSED
from acme.om.steps.types.header import ToolFailure
from acme.om.steps.types.step import Step
from acme.om.tools.impl.manager import SECRET_USED, ToolsOptions
from acme.om.tools.rules import ADVICE, call_deadline
from acme.om.tools.tool import ToolRuntime
from acme.om.tools.types.tool import Effect, ToolInput

SECRET = "ghs_9f8e7d6c5b4a3f2e1d0c-token"


def on_the_host(tmp_path: Path, ctx: TenantContext, options: ToolsOptions | None = None) -> Tools:
    """The tools manager over a directory on this host and real processes,
    with the tenant's secret in its store."""
    secrets = SecretsLocalImpl(None, {f"{ctx.org_id.hex}_api_token".upper(): SECRET})
    python = Path(sys.executable).parent
    transport = TransportLocalImpl(
        tmp_path / "records", secrets, BrokerTwinImpl(), search_path=f"{python}:{DEFAULT_PATH}"
    )
    return tools_over(transport, WorkspaceHostImpl(tmp_path / "workspaces"), options)


async def test_a_secret_is_audited_by_name_and_never_reaches_a_step_or_an_event(
    tmp_path: Path,
) -> None:
    ctx = context(Role.SERVICE, make_org())
    tools = on_the_host(tmp_path, ctx)
    tool = Command("call_api", effect=Effect.IDEMPOTENT, secrets=(INJECTED_TOKEN,))
    registry = registry_of(tool)
    workspace = await tools.manager.prepare_workspace(ctx, new_id(), HOST_SPEC)
    script = (
        "import base64, os; t = os.environ['API_TOKEN']; "
        "print(t); print(base64.b64encode(t.encode()).decode()); print(repr(t))"
    )
    found = await put_call(
        tools.steps, ctx, "call_api", {"argv": ["python3", "-c", script]}, "execute"
    )
    parts: list[str] = []

    async def sink(stream: str, text: str) -> None:
        parts.append(text)

    response = await tools.manager.execute(
        ctx,
        registry,
        found.request,
        found.call_input,
        workspace,
        epoch=found.epoch,
        tree_deadline=None,
        on_output=sink,
    )
    assert failure_of(response) is None
    text = result_text(response)
    assert text.count(marker("api_token")) == 3
    events = await tools.events.get_events(ctx, 0, 100)
    (audit,) = [event for event in events if event.kind == SECRET_USED]
    assert audit.target_id == found.request.id
    assert audit.payload["secret"] == "api_token" and audit.payload["tool"] == "call_api"
    stored = [
        step.model_dump_json()
        for step in (await tools.steps.get_steps(ctx, found.session_id, 0, 100)).items
    ]
    for held in (
        response.model_dump_json(),
        *stored,
        *(e.model_dump_json() for e in events),
        *parts,
    ):
        for form in forms(SECRET):
            assert form not in held


async def test_the_whole_tree_ends_when_the_trees_deadline_comes_first(tmp_path: Path) -> None:
    """The tool allows an hour; the tree has two seconds left. The call ends
    then, with every process it started."""
    ctx = context(Role.SERVICE, make_org())
    tools = on_the_host(tmp_path, ctx)
    registry = registry_of(Command("build", timeout=timedelta(hours=1)))
    workspace = await tools.manager.prepare_workspace(ctx, new_id(), HOST_SPEC)
    script = "sleep 600 & echo $! > child.pid; sh -c 'sleep 600' & echo $! >> child.pid; wait"
    found = await put_call(tools.steps, ctx, "build", {"argv": ["sh", "-c", script]}, "execute")
    started = utcnow()
    response = await tools.manager.execute(
        ctx,
        registry,
        found.request,
        found.call_input,
        workspace,
        epoch=found.epoch,
        tree_deadline=utcnow() + timedelta(seconds=2),
    )
    assert failure_of(response) is ToolFailure.TIMEOUT
    assert ADVICE[ToolFailure.TIMEOUT] in result_text(response)
    assert utcnow() - started < timedelta(seconds=8)
    pids = (Path(workspace.location) / "child.pid").read_text().split()
    assert len(pids) == 2
    for pid in pids:
        assert not _running(int(pid)), pid


async def test_the_tree_ends_at_its_deadline_on_a_host_with_no_ps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slim image has no `ps`: the tree is walked from `/proc` where there
    is one, and its group ends with it either way."""
    monkeypatch.setenv("PATH", str(tmp_path / "no-tools"))
    ctx = context(Role.SERVICE, make_org())
    tools = on_the_host(tmp_path, ctx)
    registry = registry_of(Command("build", timeout=timedelta(hours=1)))
    workspace = await tools.manager.prepare_workspace(ctx, new_id(), HOST_SPEC)
    script = "sleep 600 & echo $! > child.pid; sleep 600"
    found = await put_call(tools.steps, ctx, "build", {"argv": ["sh", "-c", script]}, "execute")
    response = await tools.manager.execute(
        ctx,
        registry,
        found.request,
        found.call_input,
        workspace,
        epoch=found.epoch,
        tree_deadline=utcnow() + timedelta(seconds=2),
    )
    assert failure_of(response) is ToolFailure.TIMEOUT
    (pid,) = (Path(workspace.location) / "child.pid").read_text().split()
    assert not _running(int(pid))


async def test_a_failures_text_is_bounded_as_a_result_is(tmp_path: Path) -> None:
    """A command that prints a great deal and runs out of time: its failure
    reaches the model cut to the bound, with its class and its advice."""
    ctx = context(Role.SERVICE, make_org())
    tools = on_the_host(tmp_path, ctx, ToolsOptions(max_output_chars=300))
    registry = registry_of(Command("build", timeout=timedelta(hours=1)))
    workspace = await tools.manager.prepare_workspace(ctx, new_id(), HOST_SPEC)
    script = "head -c 100000 /dev/zero | tr '\\0' x; sleep 600"
    found = await put_call(tools.steps, ctx, "build", {"argv": ["sh", "-c", script]}, "execute")
    response = await tools.manager.execute(
        ctx,
        registry,
        found.request,
        found.call_input,
        workspace,
        epoch=found.epoch,
        tree_deadline=utcnow() + timedelta(seconds=2),
    )
    text = result_text(response)
    assert failure_of(response) is ToolFailure.TIMEOUT
    assert "[cut: 300 of" in text and text.endswith(ADVICE[ToolFailure.TIMEOUT])
    assert len(text) < 300 + 200


def _running(pid: int) -> bool:
    for _ in range(30):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        time.sleep(0.1)
    return True


def test_a_calls_time_is_the_least_of_three() -> None:
    now = utcnow()
    hour, minute = timedelta(hours=1), timedelta(minutes=1)
    assert call_deadline(now, minute, hour, None) == now + minute
    assert call_deadline(now, hour, minute, None) == now + minute
    assert call_deadline(now, hour, hour, now + timedelta(seconds=5)) == now + timedelta(seconds=5)


async def test_a_non_zero_exit_is_a_result_and_not_a_failure(tmp_path: Path) -> None:
    ctx = context(Role.SERVICE, make_org())
    tools = on_the_host(tmp_path, ctx)
    registry = registry_of(Command("run_tests"))
    workspace = await tools.manager.prepare_workspace(ctx, new_id(), HOST_SPEC)
    found = await put_call(
        tools.steps, ctx, "run_tests", {"argv": ["sh", "-c", "echo 1 failed; exit 1"]}, "execute"
    )
    response = await tools.manager.execute(
        ctx,
        registry,
        found.request,
        found.call_input,
        workspace,
        epoch=found.epoch,
        tree_deadline=None,
    )
    assert failure_of(response) is None and not response.as_tool_response().is_error
    assert '"exit_code":1' in result_text(response) and "1 failed" in result_text(response)


@pytest.mark.parametrize(
    ("tool", "call_input", "failure", "says"),
    [
        ("no_such_tool", {"argv": ["true"]}, ToolFailure.INVALID_INPUT, "there is no tool named"),
        ("run_command", {"argv": "not a list"}, ToolFailure.INVALID_INPUT, "argv"),
        ("run_command", {"argv": ["true"], "sudo": True}, ToolFailure.INVALID_INPUT, "sudo"),
    ],
)
async def test_a_call_the_tool_cannot_take_is_answered_with_its_class(
    tmp_path: Path, tool: str, call_input: dict[str, Any], failure: ToolFailure, says: str
) -> None:
    transport, _ = twin_transport(tmp_path)
    tools = tools_over(transport)
    ctx = context(Role.SERVICE, make_org())
    registry = registry_of(Command())
    workspace = await tools.manager.prepare_workspace(ctx, new_id(), TWIN_SPEC)
    found = await put_call(tools.steps, ctx, tool, call_input, "execute")
    response = await tools.manager.execute(
        ctx,
        registry,
        found.request,
        found.call_input,
        workspace,
        epoch=found.epoch,
        tree_deadline=None,
    )
    assert failure_of(response) is failure and says in result_text(response)
    assert ADVICE[failure] in result_text(response)
    assert transport.commands == []


async def test_a_session_with_no_workspace_is_refused_loudly(tmp_path: Path) -> None:
    transport, _ = twin_transport(tmp_path)
    tools = tools_over(transport)
    ctx = context(Role.SERVICE, make_org())
    registry = registry_of(Command())
    no_workspace = IsolationSpec(mode=IsolationMode.NONE, egress=EgressPolicy(mode=EgressMode.NONE))
    absent = await tools.manager.prepare_workspace(ctx, new_id(), no_workspace)
    assert absent == Workspace.absent(ctx.org_id, absent.id)
    found = await put_call(tools.steps, ctx, "run_command", {"argv": ["ls"]}, "execute")
    response = await tools.manager.execute(
        ctx,
        registry,
        found.request,
        found.call_input,
        absent,
        epoch=found.epoch,
        tree_deadline=None,
    )
    assert failure_of(response) is ToolFailure.PERMANENT
    assert "this agent has no workspace" in result_text(response)


async def test_a_workspace_the_provider_cannot_meet_is_refused_and_none_is_given(
    tmp_path: Path,
) -> None:
    transport, _ = twin_transport(tmp_path)
    tools = tools_over(transport)
    ctx = context(Role.SERVICE, make_org())
    with pytest.raises(IsolationRefused):
        await tools.manager.prepare_workspace(ctx, new_id(), HOST_SPEC)


async def test_an_unsafe_tool_runs_one_command_a_call(tmp_path: Path) -> None:
    transport, _ = twin_transport(tmp_path)
    transport.handler = echoing()
    tools = tools_over(transport)
    ctx = context(Role.SERVICE, make_org())

    class Twice(Command):
        async def run(self, ctx: TenantContext, call_input: Any, runtime: Any) -> Any:
            await runtime.run(("first",))
            return await super().run(ctx, call_input, runtime)

    registry = registry_of(Twice("deploy", effect=Effect.UNSAFE))
    workspace = await tools.manager.prepare_workspace(ctx, new_id(), TWIN_SPEC)
    found = await put_call(tools.steps, ctx, "deploy", {"argv": ["second"]}, "execute")
    response = await tools.manager.execute(
        ctx,
        registry,
        found.request,
        found.call_input,
        workspace,
        epoch=found.epoch,
        tree_deadline=None,
    )
    assert failure_of(response) is ToolFailure.PERMANENT
    assert [command.argv for command in transport.commands] == [("first",)]


async def test_a_secret_the_tool_does_not_declare_is_never_given(tmp_path: Path) -> None:
    transport, _ = twin_transport(tmp_path)
    tools = tools_over(transport)
    ctx = context(Role.SERVICE, make_org())

    class Greedy(Command):
        async def run(self, ctx: TenantContext, call_input: Any, runtime: Any) -> Any:
            return await runtime.run(("env",), secrets=("api_token",))

    registry = registry_of(Greedy("look"))
    workspace = await tools.manager.prepare_workspace(ctx, new_id(), TWIN_SPEC)
    found = await put_call(tools.steps, ctx, "look", {"argv": ["env"]}, "execute")
    response = await tools.manager.execute(
        ctx,
        registry,
        found.request,
        found.call_input,
        workspace,
        epoch=found.epoch,
        tree_deadline=None,
    )
    assert failure_of(response) is ToolFailure.PERMANENT
    assert transport.commands == []
    assert [e for e in await tools.events.get_events(ctx, 0, 10) if e.kind == SECRET_USED] == []


async def test_a_stale_run_is_refused_by_the_transport(tmp_path: Path) -> None:
    transport, _ = twin_transport(tmp_path)
    tools = tools_over(transport)
    ctx = context(Role.SERVICE, make_org())
    registry = registry_of(Command())
    workspace = await tools.manager.prepare_workspace(ctx, new_id(), TWIN_SPEC)
    found = await put_call(tools.steps, ctx, "run_command", {"argv": ["ls"]}, "execute")
    later = await tools.steps.begin_run(ctx, found.session_id)
    await tools.manager.execute(
        ctx, registry, found.request, found.call_input, workspace, epoch=later, tree_deadline=None
    )
    with pytest.raises(StaleWriter):
        await tools.manager.execute(
            ctx,
            registry,
            found.request,
            found.call_input,
            workspace,
            epoch=found.epoch,
            tree_deadline=None,
        )


async def test_a_preflight_refuses_before_anyone_is_asked(tmp_path: Path) -> None:
    transport, _ = twin_transport(tmp_path)
    tools = tools_over(transport)
    ctx = context(Role.SERVICE, make_org())
    registry = registry_of(PushBranch({"feature": False}))
    workspace = Workspace.absent(ctx.org_id, new_id())
    found = await put_call(tools.steps, ctx, "push_branch", {"branch": "gone"}, "integration")
    gate = await tools.manager.gate(
        ctx, registry, KIND_DEFAULTS, found.request, found.call_input, workspace
    )
    assert gate.decision is None and gate.response is not None
    assert failure_of(gate.response) is ToolFailure.INVALID_INPUT


async def test_an_output_past_its_bound_keeps_its_head_and_its_tail_and_says_so(
    tmp_path: Path,
) -> None:
    """The end of an output, where a run's summary or the error that stopped
    it usually is, reaches the model, as its start does."""
    transport, _ = twin_transport(tmp_path)
    transport.handler = echoing()
    tools = tools_over(transport, options=ToolsOptions(max_output_chars=200))
    ctx = context(Role.SERVICE, make_org())
    registry = registry_of(Command())
    workspace = await tools.manager.prepare_workspace(ctx, new_id(), TWIN_SPEC)
    printed = "BEGIN" + "x" * 1000 + "THE END"
    found = await put_call(tools.steps, ctx, "run_command", {"argv": [printed]}, "execute")
    response = await tools.manager.execute(
        ctx,
        registry,
        found.request,
        found.call_input,
        workspace,
        epoch=found.epoch,
        tree_deadline=None,
    )
    head, cut, tail = result_text(response).split("\n")
    assert cut.startswith("[cut: 200 of") and len(head) + len(tail) == 200
    assert "BEGIN" in head and 'THE END","stderr":""}' in tail


class Flaky(Command):
    """A tool that fails as worth retrying `failures` times, then answers."""

    def __init__(self, failures: int, effect: Effect) -> None:
        super().__init__("flaky", effect=effect)
        self.failures = failures
        self.runs = 0

    async def run(
        self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime
    ) -> Platform:
        self.runs += 1
        if self.runs <= self.failures:
            raise ToolFailed(ToolFailure.TRANSIENT, "the service did not answer")
        return CommandOutput(exit_code=0, stdout="answered", stderr="")


async def flaky_call(tmp_path: Path, tool: Flaky) -> tuple[Tools, Step]:
    transport, _ = twin_transport(tmp_path)
    tools = tools_over(transport)
    ctx = context(Role.SERVICE, make_org())
    workspace = await tools.manager.prepare_workspace(ctx, new_id(), TWIN_SPEC)
    found = await put_call(tools.steps, ctx, "flaky", {"argv": ["ask"]}, "execute")
    response = await tools.manager.execute(
        ctx,
        registry_of(tool),
        found.request,
        found.call_input,
        workspace,
        epoch=found.epoch,
        tree_deadline=None,
    )
    return tools, response


@pytest.mark.parametrize("effect", [Effect.READ_ONLY, Effect.IDEMPOTENT])
async def test_a_transient_failure_of_a_tool_safe_to_repeat_is_run_again_once(
    tmp_path: Path, effect: Effect
) -> None:
    tool = Flaky(1, effect)
    tools, response = await flaky_call(tmp_path, tool)
    assert failure_of(response) is None and "answered" in result_text(response)
    assert tool.runs == 2 and tools.waits == [ToolsOptions().retry_wait.total_seconds()]


async def test_a_second_transient_failure_is_answered_and_says_it_was_run_again(
    tmp_path: Path,
) -> None:
    tool = Flaky(5, Effect.IDEMPOTENT)
    tools, response = await flaky_call(tmp_path, tool)
    assert failure_of(response) is ToolFailure.TRANSIENT and tool.runs == 2
    assert "ran it again once, and it failed again" in result_text(response)
    assert len(tools.waits) == 1


async def test_an_unsafe_tools_transient_failure_is_never_run_again(tmp_path: Path) -> None:
    tool = Flaky(1, Effect.UNSAFE)
    tools, response = await flaky_call(tmp_path, tool)
    assert failure_of(response) is ToolFailure.TRANSIENT
    assert tool.runs == 1 and tools.waits == []


@pytest.mark.parametrize("written", ['{"argv": ["ls"', "[1, 2]", "not json"])
async def test_an_input_that_is_not_one_object_is_invalid_input_and_never_runs(
    tmp_path: Path, written: str
) -> None:
    """What the model wrote for a call reaches it as invalid input it can
    correct, at the gate and if the call were run, and no command starts."""
    transport, _ = twin_transport(tmp_path)
    tools = tools_over(transport)
    ctx = context(Role.SERVICE, make_org())
    registry = registry_of(Command())
    workspace = await tools.manager.prepare_workspace(ctx, new_id(), TWIN_SPEC)
    found = await put_call(tools.steps, ctx, "run_command", {UNPARSED: written}, "execute")
    gate = await tools.manager.gate(
        ctx, registry, KIND_DEFAULTS, found.request, found.call_input, workspace
    )
    ran = await tools.manager.execute(
        ctx,
        registry,
        found.request,
        found.call_input,
        workspace,
        epoch=found.epoch,
        tree_deadline=None,
    )
    for answered in (gate.response, ran):
        assert answered is not None and failure_of(answered) is ToolFailure.INVALID_INPUT
        assert "not one JSON object, so the call never ran" in result_text(answered)
    assert transport.commands == []
