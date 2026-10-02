"""The registry is an agent's power: a call resolves only to a tool it holds,
in a fixed order, under one contract. A tool over MCP takes that contract:
its class and its effect are the adopter's, its definition is pinned by
hash, and the server's own hints decide nothing. A job starts work that
outlives the run, by a deadline never later than the tree's."""

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from contracts.doubles import context
from contracts.factories import make_org
from contracts.tools import (
    TWIN_SPEC,
    Command,
    PushBranch,
    failure_of,
    put_call,
    registry_of,
    tools_over,
    twin_transport,
)
from pydantic import ValidationError

from acme.om.base import Platform, new_id, utcnow
from acme.om.context import Role, TenantContext
from acme.om.exceptions import McpDefinitionChanged, ValidationFailed
from acme.om.steps.types.header import ToolFailure
from acme.om.tools.mcp import McpToolImpl
from acme.om.tools.registry import ToolRegistry
from acme.om.tools.rules import definition_hash
from acme.om.tools.tool import JobToolInterface, ToolRuntime
from acme.om.tools.types.call import JobHandle, JobStarted
from acme.om.tools.types.mcp import McpBinding, McpToolDefinition
from acme.om.tools.types.policy import Target
from acme.om.tools.types.tool import Effect, ToolClass, ToolInput, ToolMode, ToolSpec


def test_the_registry_renders_in_a_fixed_order_whatever_the_order_given() -> None:
    tools = [Command("zip_logs"), PushBranch({}), Command("apply_patch")]
    one, other = ToolRegistry(tools), ToolRegistry(reversed(tools))
    assert [d.name for d in one.render()] == ["apply_patch", "push_branch", "zip_logs"]
    assert one.render() == other.render()
    assert one.classes() == {"execute", "integration"}


def test_the_schema_the_model_reads_refuses_unknown_fields() -> None:
    (definition,) = registry_of(PushBranch({})).render()
    assert definition.input_schema["additionalProperties"] is False


def test_a_name_the_registry_does_not_hold_resolves_to_nothing() -> None:
    registry = registry_of(Command())
    assert registry.get("run_command") is not None
    assert registry.get("rm_rf") is None


def test_the_registry_refuses_what_would_slip_past_policy() -> None:
    with pytest.raises(ValueError, match="one tool of each name"):
        ToolRegistry([Command(), Command()])
    with pytest.raises(ValueError, match="no class"):
        ToolRegistry([Command(authorization_class="destrutive")])
    hardware = Command("move_arm", authorization_class="hardware")
    assert ToolRegistry([hardware], domain_classes=["hardware"]).get("move_arm") is hardware
    with pytest.raises(ValueError, match="job mode"):
        ToolRegistry([Command("train", mode=ToolMode.JOB)])


def test_a_tool_declares_its_whole_contract() -> None:
    fields = set(ToolSpec.model_fields)
    assert {
        "name",
        "description",
        "input_model",
        "output_model",
        "timeout",
        "authorization_class",
        "effect",
        "interruptible",
        "mode",
    } <= fields
    with pytest.raises(ValidationError):
        ToolSpec.model_validate(
            {"name": "x", "description": "x", "input_model": ToolInput, "output_model": Platform}
        )


# Tools over MCP.


class IssueInput(ToolInput):
    number: int


DEFINITION = McpToolDefinition(
    name="close_issue",
    description="Closes an issue.",
    input_schema={"type": "object", "properties": {"number": {"type": "integer"}}},
    annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
)


def binding(**changes: Any) -> McpBinding:
    return McpBinding.model_validate(
        {
            "server": "tracker",
            "server_tool": "close_issue",
            "name": "close_issue",
            "input_model": IssueInput,
            "timeout": timedelta(seconds=30),
            "authorization_class": ToolClass.INTEGRATION,
            "effect": Effect.UNSAFE,
            "target": Target(attributes={"outward": True}),
            "definition_hash": definition_hash(DEFINITION),
            **changes,
        }
    )


async def test_an_mcp_tool_takes_its_class_and_effect_from_the_binding_never_the_server() -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    async def call(ctx: TenantContext, server: str, tool: str, call_input: Any) -> str:
        calls.append((server, tool, dict(call_input)))
        return "closed"

    tool = McpToolImpl(DEFINITION, binding(), call)
    assert tool.spec.effect is Effect.UNSAFE, "the server's read-only hint decides nothing"
    assert tool.spec.authorization_class == "integration"
    assert tool.spec.description == "Closes an issue."
    ctx = context(Role.SERVICE, make_org())
    assert (await tool.target(ctx, IssueInput(number=7))).attributes == {"outward": True}
    registry = registry_of(tool)
    assert registry.get("close_issue") is tool


async def test_a_changed_definition_is_not_served_until_its_pin_moves() -> None:
    async def call(ctx: TenantContext, server: str, tool: str, call_input: Any) -> str:
        raise AssertionError("not served")

    for changed in (
        DEFINITION.model_copy(update={"description": "Closes an issue, and deletes the repo."}),
        DEFINITION.model_copy(update={"annotations": {"readOnlyHint": False}}),
        DEFINITION.model_copy(update={"input_schema": {"type": "object"}}),
    ):
        with pytest.raises(McpDefinitionChanged):
            McpToolImpl(changed, binding(), call)
        McpToolImpl(changed, binding(definition_hash=definition_hash(changed)), call)
    with pytest.raises(McpDefinitionChanged):
        McpToolImpl(DEFINITION.model_copy(update={"name": "open_issue"}), binding(), call)


async def test_an_mcp_call_goes_through_the_adopters_client_with_the_typed_input(
    tmp_path: Path,
) -> None:
    transport, _ = twin_transport(tmp_path)
    tools = tools_over(transport)
    ctx = context(Role.SERVICE, make_org())
    seen: list[Any] = []

    async def call(ctx: TenantContext, server: str, tool: str, call_input: Any) -> str:
        seen.append((server, tool, call_input))
        return "closed #7"

    registry = registry_of(McpToolImpl(DEFINITION, binding(), call))
    workspace = await tools.manager.prepare_workspace(ctx, new_id(), TWIN_SPEC)
    found = await put_call(tools.steps, ctx, "close_issue", {"number": 7}, "integration")
    response = await tools.manager.execute(
        ctx,
        registry,
        found.request,
        found.call_input,
        workspace,
        epoch=found.epoch,
        tree_deadline=None,
    )
    assert failure_of(response) is None and "closed #7" in response.model_dump_json()
    assert seen == [("tracker", "close_issue", {"number": 7})]


# Jobs.


class TrainInput(ToolInput):
    epochs: int


class Train(JobToolInterface):
    def __init__(self) -> None:
        self.started: dict[str, Any] = {}
        self.cancelled: list[JobHandle] = []

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="train",
            description="Trains a policy.",
            input_model=TrainInput,
            output_model=JobStarted,
            timeout=timedelta(hours=6),
            authorization_class=ToolClass.EXECUTE,
            effect=Effect.IDEMPOTENT,
            interruptible=True,
            mode=ToolMode.JOB,
        )

    async def target(self, ctx: TenantContext, call_input: ToolInput) -> Target:
        return Target()

    async def preflight(
        self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime
    ) -> None:
        return None

    async def run(
        self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime
    ) -> Platform:
        # Started under the call's key: starting it again attaches.
        handle = self.started.setdefault("job-1", runtime.deadline)
        assert handle == runtime.deadline
        return JobStarted(handle="job-1")

    async def cancel(self, ctx: TenantContext, job: JobHandle) -> None:
        self.cancelled.append(job)


async def test_a_job_starts_by_a_deadline_no_later_than_the_trees(tmp_path: Path) -> None:
    transport, _ = twin_transport(tmp_path)
    tools = tools_over(transport)
    ctx = context(Role.SERVICE, make_org())
    train = Train()
    registry = registry_of(train)
    workspace = await tools.manager.prepare_workspace(ctx, new_id(), TWIN_SPEC)
    found = await put_call(tools.steps, ctx, "train", {"epochs": 3}, "execute")
    tree_deadline = tools.clock.now + timedelta(hours=1)
    job = await tools.manager.start_job(
        ctx,
        registry,
        found.request,
        found.call_input,
        workspace,
        epoch=found.epoch,
        tree_deadline=tree_deadline,
    )
    assert isinstance(job, JobHandle)
    assert (job.key, job.handle, job.deadline) == (found.request.id, "job-1", tree_deadline)
    again = await tools.manager.start_job(
        ctx,
        registry,
        found.request,
        found.call_input,
        workspace,
        epoch=found.epoch,
        tree_deadline=tree_deadline,
    )
    assert again == job, "a recovered run attaches to the job it started"
    await tools.manager.cancel_job(ctx, registry, job)
    assert train.cancelled == [job]
    with pytest.raises(ValidationFailed, match="job"):
        await tools.manager.execute(
            ctx,
            registry,
            found.request,
            found.call_input,
            workspace,
            epoch=found.epoch,
            tree_deadline=None,
        )


async def test_a_job_that_will_not_start_is_answered_with_its_failure(tmp_path: Path) -> None:
    transport, _ = twin_transport(tmp_path)
    tools = tools_over(transport)
    ctx = context(Role.SERVICE, make_org())
    registry = registry_of(Train())
    workspace = await tools.manager.prepare_workspace(ctx, new_id(), TWIN_SPEC)
    found = await put_call(tools.steps, ctx, "train", {"epochs": "many"}, "execute")
    answered = await tools.manager.start_job(
        ctx,
        registry,
        found.request,
        found.call_input,
        workspace,
        epoch=found.epoch,
        tree_deadline=utcnow(),
    )
    assert not isinstance(answered, JobHandle) and failure_of(answered) is ToolFailure.INVALID_INPUT
