"""The step on its own: what its type answers, the views over its content,
the shapes it refuses when it is built, and the steps manager over the
memory storage. A step is built from what a model returns, so every
malformed shape is refused at construction and never stored."""

from pathlib import Path
from typing import Any

import pytest
from contracts.doubles import context
from contracts.step_storage import (
    make_message,
    make_parked,
    make_request,
    make_response,
    make_tool_request,
    make_tool_response,
)
from pydantic import ValidationError

from acme.infra.impl.local import InfraLocalImpl
from acme.om.base import new_id
from acme.om.context import Role
from acme.om.exceptions import NotAuthorized, StaleWriter, ValidationFailed
from acme.om.root import build_managers
from acme.om.steps import StepsManagerInterface
from acme.om.steps.types.content import MAX_NAME, ToolResultBlock, ToolUseBlock
from acme.om.steps.types.page import StepCursor
from acme.om.steps.types.step import (
    BLOCK_KINDS,
    FAMILIES,
    HEADER_KINDS,
    Step,
    StepFamily,
    StepType,
)
from acme.om.storage.impl.memory import StorageMemoryImpl

SESSION = new_id()


def raw(step: Step, **changes: Any) -> dict[str, Any]:
    """A step as JSON, the shape a provider adapter or a stored row hands
    in, with some fields changed."""
    return {**step.model_dump(mode="json"), **changes}


# What the type answers.


def test_every_type_has_its_family_its_header_and_its_blocks() -> None:
    assert set(FAMILIES) == set(HEADER_KINDS) == set(BLOCK_KINDS) == set(StepType)


def test_the_type_answers_questions() -> None:
    assert StepType.TOOL_REQUEST.is_tool_call() and StepType.TOOL_RESPONSE.is_tool_call()
    assert StepType.MODEL_REQUEST.is_model_call() and StepType.MODEL_RESPONSE.is_model_call()
    assert StepType.MESSAGE.is_input() and StepType.EVENT.is_input()
    assert StepType.SUMMARY.is_summary() and not StepType.MESSAGE.is_summary()
    assert StepType.CONTROL.is_control() and not StepType.CONTROL.is_input()
    assert StepType.LOOP_ENDED.is_lifecycle() and StepType.PARKED.family is StepFamily.LIFECYCLE
    responses = {t for t in StepType if t.is_response()}
    assert responses == {StepType.MODEL_RESPONSE, StepType.TOOL_RESPONSE}


# The views.


def test_the_views_read_the_blocks_of_their_type() -> None:
    message = make_message(SESSION, "drops it")
    request = make_request(SESSION, message.id)
    response = make_response(SESSION, message.id, request.id)
    result = make_tool_response(SESSION, message.id, new_id())
    assert message.as_text() == "drops it"
    assert response.as_text() == "reading the controller log"
    assert [use.name for use in response.as_tool_uses()] == ["read_log"]
    assert result.as_tool_response().tool_use_id == "call_1"
    assert request.as_text() == ""
    with pytest.raises(ValueError, match="is not a tool_response"):
        response.as_tool_response()
    with pytest.raises(ValueError, match="is not a model_response"):
        message.as_tool_uses()


def test_a_valid_step_survives_its_json() -> None:
    for step in (
        make_message(SESSION),
        make_response(SESSION, new_id(), new_id()),
        make_tool_response(SESSION, new_id(), new_id()),
        make_parked(SESSION, new_id()),
    ):
        assert Step.model_validate(raw(step)) == step


# The shapes a step refuses when it is built: what a model sends that the
# block model has no place for is refused there, never carried on.


def malformed() -> list[tuple[str, dict[str, Any]]]:
    message = make_message(SESSION)
    request = make_request(SESSION, message.id)
    response = make_response(SESSION, message.id, request.id)
    call = make_tool_request(SESSION, message.id, response.id)
    result = make_tool_response(SESSION, message.id, call.id)
    use = {"kind": "tool_use", "id": "c", "name": "read_log", "input": {}}
    return [
        ("a block of no known kind", raw(message, content={"blocks": [{"kind": "audio"}]})),
        (
            "a block with a field no block has",
            raw(message, content={"blocks": [{"kind": "text", "text": "x", "tokens": 4}]}),
        ),
        ("text that is not text", raw(message, content={"blocks": [{"kind": "text", "text": 5}]})),
        (
            "a tool input that is not a mapping",
            raw(response, content={"blocks": [{**use, "input": "rm -rf /"}]}),
        ),
        (
            "a tool name past the bound",
            raw(response, content={"blocks": [{**use, "name": "x" * (MAX_NAME + 1)}]}),
        ),
        ("a tool use with no id", raw(response, content={"blocks": [{**use, "id": ""}]})),
        ("two tool uses with one id", raw(response, content={"blocks": [use, use]})),
        ("a response that names no request", raw(response, responds_to=None)),
        ("a request that names a request", raw(request, responds_to=str(new_id()))),
        (
            "a model request that copies what it carried",
            raw(request, content={"blocks": [{"kind": "text", "text": "copied"}]}),
        ),
        ("a tool request that copies the tool use", raw(call, content={"blocks": [use]})),
        ("a tool request that names no response", raw(call, refs=[])),
        (
            "a tool response with two results",
            raw(
                result,
                content={
                    "blocks": [
                        {"kind": "tool_result", "tool_use_id": "a"},
                        {"kind": "tool_result", "tool_use_id": "b"},
                    ]
                },
            ),
        ),
        (
            "an image with no placeholder",
            raw(message, content={"blocks": [{"kind": "image", "attachment_id": str(new_id())}]}),
        ),
        (
            "thinking on a message",
            raw(message, children={"thinking": [{"kind": "thinking", "text": "hm"}]}),
        ),
        (
            "thinking as main content",
            raw(response, content={"blocks": [{"kind": "thinking", "text": "hm"}]}),
        ),
        ("a header of another type", raw(message, header={"kind": "model_request", "role": "m"})),
        ("a header of no known kind", raw(message, header={"kind": "turn"})),
        ("a type of no known name", raw(message, type="turn")),
        ("a negative seq", raw(message, seq=-1)),
        ("a step that references itself", raw(request, refs=[str(request.id)])),
        (
            "a summary that ends before it starts",
            raw(message, type="summary", header={"kind": "summary", "first_seq": 5, "last_seq": 2}),
        ),
    ]


@pytest.mark.parametrize(("what", "shape"), malformed(), ids=[w for w, _ in malformed()])
def test_a_malformed_step_is_refused_when_it_is_built(what: str, shape: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Step.model_validate(shape)


def test_a_tool_result_holds_text_and_files_alone() -> None:
    with pytest.raises(ValidationError):
        ToolResultBlock.model_validate(
            {"tool_use_id": "a", "parts": [{"kind": "tool_use", "id": "b", "name": "n"}]}
        )
    assert ToolUseBlock(id="a", name="n").input == {}


# The manager, over the memory storage.


@pytest.fixture
def steps(tmp_path: Path) -> StepsManagerInterface:
    return build_managers(StorageMemoryImpl(), InfraLocalImpl(tmp_path)).steps


async def test_a_run_appends_under_its_epoch_and_reads_its_history(
    steps: StepsManagerInterface,
) -> None:
    ctx = context(Role.MEMBER)
    session = new_id()
    (first,) = await steps.append_inputs(ctx, session, [make_message(session)])
    epoch = await steps.begin_run(ctx, session)
    request = make_request(session, first.id, (first.id,))
    response = make_response(session, first.id, request.id)
    await steps.append_steps(ctx, session, epoch, [request, response])
    page = await steps.get_steps(ctx, session, 0, 2)
    assert [s.seq for s in page.items] == [1, 2] and page.has_more
    rest = await steps.get_steps(ctx, session, 2, 10)
    assert [s.id for s in rest.items] == [response.id] and not rest.has_more
    assert await steps.get_cursor(ctx, session) == StepCursor(head=3, epoch=epoch)
    later = await steps.begin_run(ctx, session)
    with pytest.raises(StaleWriter):
        await steps.append_steps(ctx, session, epoch, [make_message(session)])
    assert (await steps.get_cursor(ctx, session)).epoch == later


async def test_an_append_is_bounded_and_a_viewer_writes_nothing(
    steps: StepsManagerInterface,
) -> None:
    session = new_id()
    member = context(Role.MEMBER)
    with pytest.raises(ValidationFailed):
        await steps.append_inputs(member, session, [make_message(session) for _ in range(101)])
    viewer = context(Role.VIEWER)
    with pytest.raises(NotAuthorized):
        await steps.append_inputs(viewer, session, [make_message(session)])
    with pytest.raises(NotAuthorized):
        await steps.begin_run(viewer, session)
    assert (await steps.get_steps(viewer, session, 0, 10)).items == ()
