"""A new run settles a request a lost run left open by what its tool's
effect allows. A `read_only` or `idempotent` call runs again under the same
key, the request's id. An `unsafe` one is never repeated: the transport's
record of how its command ended answers it, and with none it is
`interrupted`, its outcome unknown."""

from pathlib import Path

import pytest
from contracts.doubles import context
from contracts.factories import make_org
from contracts.tools import (
    TWIN_SPEC,
    Command,
    PushBranch,
    echoing,
    failure_of,
    put_call,
    registry_of,
    result_text,
    tools_over,
    twin_transport,
)

from acme.om.base import new_id
from acme.om.context import Role
from acme.om.steps.types.header import ToolFailure
from acme.om.tools.rules import engine_retries
from acme.om.tools.types.tool import Effect


@pytest.mark.parametrize("effect", [Effect.READ_ONLY, Effect.IDEMPOTENT])
async def test_a_repeatable_call_runs_again_under_the_same_key(
    tmp_path: Path, effect: Effect
) -> None:
    transport, _ = twin_transport(tmp_path)
    transport.handler = echoing()
    tools = tools_over(transport)
    ctx = context(Role.SERVICE, make_org())
    registry = registry_of(Command(effect=effect))
    workspace = await tools.manager.prepare_workspace(ctx, new_id(), TWIN_SPEC)
    found = await put_call(tools.steps, ctx, "run_command", {"argv": ["make", "test"]}, "execute")
    # The lost run's command ran; its response never reached the history.
    await tools.manager.execute(
        ctx,
        registry,
        found.request,
        found.call_input,
        workspace,
        epoch=found.epoch,
        tree_deadline=None,
    )
    later = await tools.steps.begin_run(ctx, found.session_id)
    settled = await tools.manager.recover(
        ctx, registry, found.request, found.call_input, workspace, epoch=later, tree_deadline=None
    )
    assert failure_of(settled) is None and "make test" in result_text(settled)
    assert [command.key for command in transport.commands] == [found.request.id] * 2
    assert [command.epoch for command in transport.commands] == [found.epoch, later]


async def test_an_unsafe_call_is_answered_from_the_transports_record(tmp_path: Path) -> None:
    transport, _ = twin_transport(tmp_path)
    transport.handler = echoing()
    tools = tools_over(transport)
    ctx = context(Role.SERVICE, make_org())
    registry = registry_of(Command("deploy", effect=Effect.UNSAFE))
    workspace = await tools.manager.prepare_workspace(ctx, new_id(), TWIN_SPEC)
    found = await put_call(tools.steps, ctx, "deploy", {"argv": ["deploy", "staging"]}, "execute")
    await tools.manager.execute(
        ctx,
        registry,
        found.request,
        found.call_input,
        workspace,
        epoch=found.epoch,
        tree_deadline=None,
    )
    later = await tools.steps.begin_run(ctx, found.session_id)
    settled = await tools.manager.recover(
        ctx, registry, found.request, found.call_input, workspace, epoch=later, tree_deadline=None
    )
    assert failure_of(settled) is None
    assert "transport's record" in result_text(settled) and "deploy staging" in result_text(settled)
    assert len(transport.commands) == 1, "the unsafe command is never run again"


async def test_an_unsafe_call_with_no_record_is_interrupted_and_never_repeated(
    tmp_path: Path,
) -> None:
    transport, _ = twin_transport(tmp_path)
    tools = tools_over(transport)
    ctx = context(Role.SERVICE, make_org())
    push = PushBranch({"feature": False})
    registry = registry_of(Command("deploy", effect=Effect.UNSAFE), push)
    workspace = await tools.manager.prepare_workspace(ctx, new_id(), TWIN_SPEC)
    for tool, call_input, cls in (
        ("deploy", {"argv": ["deploy", "staging"]}, "execute"),
        ("push_branch", {"branch": "feature"}, "integration"),
    ):
        found = await put_call(tools.steps, ctx, tool, call_input, cls)
        later = await tools.steps.begin_run(ctx, found.session_id)
        settled = await tools.manager.recover(
            ctx,
            registry,
            found.request,
            found.call_input,
            workspace,
            epoch=later,
            tree_deadline=None,
        )
        assert failure_of(settled) is ToolFailure.INTERRUPTED, tool
        assert "Check the state it would have changed" in result_text(settled)
    assert transport.commands == [] and push.pushed == []


def test_the_engine_retries_only_a_transient_failure_of_a_repeatable_tool() -> None:
    for failure in ToolFailure:
        for effect in Effect:
            expected = failure is ToolFailure.TRANSIENT and effect is not Effect.UNSAFE
            assert engine_retries(failure, effect) is expected, (failure, effect)
