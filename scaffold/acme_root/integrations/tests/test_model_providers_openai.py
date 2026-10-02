"""The OpenAI adapter over recorded payloads, with no network: the one
content shape both ways, what does not survive named and dropped, and each
provider error read into its kind. `openai_tool_use.sse` is a real streamed
response, recorded once; the reasoning item and the error bodies are in the
provider's documented shapes."""

import json
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from acme.integrations.model_providers import reply_of
from acme.integrations.model_providers.calls import (
    FileData,
    Message,
    ModelCall,
    OutputSchema,
    StreamPart,
    ThinkingDelta,
    ToolUseDelta,
)
from acme.integrations.model_providers.content import (
    DocumentBlock,
    ImageBlock,
    TextBlock,
    ThinkingBlock,
    ThinkingSource,
    ToolResultBlock,
    ToolUseBlock,
)
from acme.integrations.model_providers.failures import ModelCallFailed
from acme.integrations.model_providers.openai import (
    ModelProviderOpenAIImpl,
    OpenAIReply,
    classify,
    request_body,
)
from acme.integrations.model_providers.types import (
    Effort,
    ErrorKind,
    ProviderName,
    StopReason,
    Usage,
)
from acme.integrations.model_providers.wire import sse_events

FIXTURES = Path(__file__).parent / "fixtures" / "model_providers"
RECORDED = (FIXTURES / "openai_tool_use.sse").read_text()
MODEL = "gpt-6-luna"
KEY = SecretStr("test-key-not-a-secret")
ASK = Message(role="user", blocks=(TextBlock(text="What is 17 + 25? Use the add tool."),))


async def _lines(text: str) -> AsyncIterator[str]:
    for line in text.splitlines():
        yield line


async def fold(text: str, model: str = MODEL) -> tuple[list[StreamPart], OpenAIReply]:
    folded = OpenAIReply(model)
    parts: list[StreamPart] = []
    async for event, data in sse_events(_lines(text)):
        parts.extend(folded.feed(event, data))
    return parts, folded


def sse(*events: dict[str, Any]) -> str:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)


USAGE = {
    "input_tokens": 1200,
    "input_tokens_details": {"cached_tokens": 1000, "cache_write_tokens": 50},
    "output_tokens": 300,
    "output_tokens_details": {"reasoning_tokens": 120},
}
REASONING = {
    "id": "rs_1",
    "type": "reasoning",
    "summary": [{"type": "summary_text", "text": "Add the two numbers."}],
    "encrypted_content": "gAAAA-opaque",
}


def finished(
    status: str, output: list[dict[str, Any]], reason: str | None = None
) -> dict[str, Any]:
    response = {
        "id": "resp_1",
        "model": MODEL,
        "status": status,
        "output": output,
        "usage": USAGE,
        "incomplete_details": None if reason is None else {"reason": reason},
    }
    kind = "response.completed" if status == "completed" else "response.incomplete"
    return {"type": kind, "response": response}


def message(*parts: dict[str, Any]) -> dict[str, Any]:
    return {"type": "message", "role": "assistant", "content": list(parts)}


# The reply, from the provider's events.


async def test_a_recorded_tool_call_reads_as_the_one_shape() -> None:
    parts, folded = await fold(RECORDED)
    reply = folded.reply()
    assert reply.blocks == (
        ToolUseBlock(id="call_p7wkrtUVNYYNqj1bVwydJKaI", name="add", input={"a": 17, "b": 25}),
    )
    assert (reply.stop_reason, reply.truncated, reply.thinking) == (StopReason.TOOL_USE, False, ())
    assert reply.usage == Usage(input=71, output=21)
    assert reply.model == MODEL and reply.dropped == ()
    assert reply.response_id == "resp_03926ee8d6e329cf016abf6ec193a887d1852caefd8c1d84a8"
    fragments = [p for p in parts if isinstance(p, ToolUseDelta)]
    assert json.loads("".join(p.partial_input for p in fragments)) == {"a": 17, "b": 25}


async def test_cached_written_and_reasoning_tokens_are_taken_out_of_their_counts() -> None:
    _, folded = await fold(
        sse(finished("completed", [REASONING, message({"type": "output_text", "text": "42"})]))
    )
    reply = folded.reply()
    assert reply.usage == Usage(
        input=150, cache_read=1000, cache_write=50, output=180, thinking=120
    )
    assert reply.usage.prompt == 1200
    (thought,) = reply.thinking
    assert thought == ThinkingBlock(
        text="Add the two numbers.",
        signature="gAAAA-opaque",
        source=ThinkingSource(provider=ProviderName.OPENAI, model=MODEL),
        ref="rs_1",
        at=0,
    )
    assert reply.blocks == (TextBlock(text="42"),) and reply.stop_reason is StopReason.END_TURN


async def test_a_refusal_is_a_response_and_an_unknown_item_is_named_and_dropped() -> None:
    output = [
        {"type": "web_search_call", "id": "ws_1", "status": "completed"},
        message({"type": "refusal", "refusal": "I can't help with that."}),
    ]
    _, folded = await fold(sse(finished("completed", output)))
    reply = folded.reply()
    assert reply.stop_reason is StopReason.REFUSAL and not reply.truncated
    assert reply.blocks == (TextBlock(text="I can't help with that."),)
    assert [d.what for d in reply.dropped] == ["output item web_search_call"]


@pytest.mark.parametrize(
    "reason,stop",
    [
        ("max_output_tokens", StopReason.OUTPUT_LIMIT),
        ("content_filter", StopReason.CONTENT_FILTER),
        ("new", None),
    ],
)
async def test_an_incomplete_response_is_never_whole(reason: str, stop: StopReason | None) -> None:
    _, folded = await fold(
        sse(finished("incomplete", [message({"type": "output_text", "text": "4"})], reason))
    )
    reply = folded.reply()
    assert reply.stop_reason is stop
    assert reply.truncated is (stop is not StopReason.CONTENT_FILTER)


@pytest.mark.parametrize("arguments", ['{"a": 1', "[1]", "not json"])
async def test_a_malformed_tool_call_is_dropped_and_never_runs(arguments: str) -> None:
    call = {"type": "function_call", "call_id": "call_1", "name": "add", "arguments": arguments}
    _, folded = await fold(sse(finished("completed", [call])))
    reply = folded.reply()
    assert reply.blocks == () and reply.dropped[0].what == "tool use call_1"
    assert reply.stop_reason is StopReason.END_TURN


async def test_a_failed_response_fails_with_its_kind_and_what_arrived() -> None:
    events = [
        {"type": "response.created", "response": {"id": "resp_1", "model": MODEL}},
        {"type": "response.output_text.delta", "output_index": 0, "delta": "Adding"},
        {
            "type": "response.failed",
            "response": {"id": "resp_1", "error": {"code": "server_error", "message": "boom"}},
        },
    ]
    with pytest.raises(ModelCallFailed) as failed:
        await fold(sse(*events))
    assert failed.value.kind is ErrorKind.TRANSIENT
    partial = failed.value.partial
    assert (
        partial is not None and partial.truncated and partial.blocks == (TextBlock(text="Adding"),)
    )


async def test_a_stream_that_ends_early_is_broken_and_never_whole() -> None:
    events = [
        {"type": "response.output_item.added", "output_index": 0, "item": REASONING},
        {"type": "response.reasoning_summary_text.delta", "output_index": 0, "delta": "Add"},
        {"type": "response.output_item.done", "output_index": 0, "item": REASONING},
    ]
    parts, folded = await fold(sse(*events) + "data: {cut")
    assert [p.text for p in parts if isinstance(p, ThinkingDelta)] == ["Add"]
    with pytest.raises(ModelCallFailed) as failed:
        folded.reply()
    assert failed.value.kind is ErrorKind.TRANSIENT
    assert failed.value.partial is not None and len(failed.value.partial.thinking) == 1


# The request, from the one shape.


def continuation(model: str, thought: ThinkingBlock) -> ModelCall:
    use = ToolUseBlock(id="call_1", name="add", input={"a": 17, "b": 25})
    return ModelCall(
        model=model,
        system=(TextBlock(text="You are terse."),),
        messages=(
            ASK,
            Message(role="assistant", blocks=(thought, use)),
            Message(
                role="user",
                blocks=(
                    ToolResultBlock(
                        tool_use_id="call_1", parts=(TextBlock(text="42"),), is_error=True
                    ),
                    TextBlock(text="Thanks."),
                ),
            ),
        ),
        max_output_tokens=1024,
        effort=Effort.LOW,
    )


SOURCE = ThinkingSource(provider=ProviderName.OPENAI, model=MODEL)


def test_reasoning_replays_with_its_signature_to_the_model_that_thought_it() -> None:
    thought = ThinkingBlock(
        text="Add the two numbers.", signature="gAAAA-opaque", source=SOURCE, ref="rs_1"
    )
    body, dropped = request_body(continuation(MODEL, thought))
    assert body["input"] == [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "What is 17 + 25? Use the add tool."}],
        },
        {
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "Add the two numbers."}],
            "encrypted_content": "gAAAA-opaque",
            "id": "rs_1",
        },
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "add",
            "arguments": '{"a":17,"b":25}',
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "42"},
        {"role": "user", "content": [{"type": "input_text", "text": "Thanks."}]},
    ]
    assert body["instructions"] == "You are terse." and body["store"] is False
    assert body["reasoning"] == {"effort": "low", "summary": "auto"}
    assert body["include"] == ["reasoning.encrypted_content"]
    assert [d.what for d in dropped] == ["a tool result's error flag"]


@pytest.mark.parametrize(
    "thought",
    [
        ThinkingBlock(
            text="t",
            signature="s",
            source=ThinkingSource(provider=ProviderName.OPENAI, model="gpt-6.1-sol"),
        ),
        ThinkingBlock(
            text="t",
            signature="s",
            source=ThinkingSource(provider=ProviderName.ANTHROPIC, model=MODEL),
        ),
        ThinkingBlock(text="t", source=SOURCE),
    ],
)
def test_thinking_another_model_thought_or_unsigned_is_named_and_dropped(
    thought: ThinkingBlock,
) -> None:
    body, dropped = request_body(continuation(MODEL, thought))
    assert not [item for item in body["input"] if item.get("type") == "reasoning"]
    assert dropped[0].what == "thinking"


def test_cache_markers_and_a_thinking_budget_are_named_and_dropped() -> None:
    call = ModelCall(
        model=MODEL,
        system=(TextBlock(text="kind prompt"),),
        system_cache=True,
        messages=(ASK.model_copy(update={"cache": True}),),
        max_output_tokens=100,
        thinking_budget=2048,
    )
    body, dropped = request_body(call)
    assert "cache_control" not in json.dumps(body) and "reasoning" not in body
    assert [d.what for d in dropped] == ["2 cache marker(s)", "a thinking budget"]


def test_files_render_as_data_urls_and_a_tool_results_files_are_dropped() -> None:
    image, pdf = uuid4(), uuid4()
    call = ModelCall(
        model=MODEL,
        messages=(
            Message(
                role="user",
                blocks=(
                    ImageBlock(attachment_id=image),
                    DocumentBlock(attachment_id=pdf),
                    ToolResultBlock(tool_use_id="call_1", parts=(ImageBlock(attachment_id=image),)),
                ),
            ),
        ),
        max_output_tokens=100,
        files=(
            FileData(attachment_id=image, name="chart.png", media_type="image/png", data=b"png"),
            FileData(
                attachment_id=pdf, name="report.pdf", media_type="application/pdf", data=b"pdf"
            ),
        ),
    )
    body, dropped = request_body(call)
    assert body["input"][0] == {"type": "function_call_output", "call_id": "call_1", "output": ""}
    assert body["input"][1]["content"] == [
        {"type": "input_image", "image_url": "data:image/png;base64,cG5n"},
        {
            "type": "input_file",
            "filename": "report.pdf",
            "file_data": "data:application/pdf;base64,cGRm",
        },
    ]
    assert [d.what for d in dropped] == ["image in a tool result"]


def test_a_schema_maps_to_the_text_format() -> None:
    schema = OutputSchema(name="verdict", json_schema={"type": "object"})
    body, _ = request_body(
        ModelCall(model=MODEL, messages=(ASK,), max_output_tokens=10, output_schema=schema)
    )
    assert body["text"] == {
        "format": {
            "type": "json_schema",
            "name": "verdict",
            "schema": {"type": "object"},
            "strict": False,
        }
    }


# The kind of an error, from its status and its message.


ERRORS = json.loads((FIXTURES / "openai_errors.json").read_text())


@pytest.mark.parametrize("case", ERRORS, ids=lambda c: f"{c['status']}-{c['kind']}")
def test_each_error_is_read_into_its_kind(case: dict[str, Any]) -> None:
    body = case["body"]
    kind, message = classify(
        case["status"], body if case["status"] is None else json.dumps(body).encode()
    )
    assert kind is ErrorKind(case["kind"])
    assert message


def test_the_cases_cover_every_kind() -> None:
    assert {ErrorKind(c["kind"]) for c in ERRORS} == set(ErrorKind)


# The adapter over HTTP, with the provider stood in by a transport.


def adapter(
    handler: Callable[[httpx.Request], httpx.Response], key: SecretStr | None = KEY
) -> ModelProviderOpenAIImpl:
    return ModelProviderOpenAIImpl(
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        api_key=key,
        base_url="https://provider.test",
        timeout=timedelta(seconds=5),
    )


CALL = ModelCall(model=MODEL, messages=(ASK,), max_output_tokens=1024, effort=Effort.LOW)


async def test_a_call_streams_the_recorded_reply_over_http() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=RECORDED, headers={"content-type": "text/event-stream"})

    reply = await reply_of(adapter(handler).stream(CALL))
    assert reply.stop_reason is StopReason.TOOL_USE
    (request,) = seen
    assert request.url == "https://provider.test/v1/responses"
    assert request.headers["authorization"] == f"Bearer {KEY.get_secret_value()}"
    assert json.loads(request.content) == request_body(CALL)[0]


async def test_a_spent_quota_on_a_rate_limit_status_is_billing_and_never_retried() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = {
            "error": {
                "message": "You exceeded your current quota, please check your plan and billing details.",
                "type": "insufficient_quota",
                "code": "insufficient_quota",
            }
        }
        return httpx.Response(429, json=body, headers={"retry-after-ms": "1500"})

    with pytest.raises(ModelCallFailed) as failed:
        await reply_of(adapter(handler).stream(CALL))
    assert (failed.value.kind, failed.value.status, failed.value.retry_after) == (
        ErrorKind.BILLING,
        429,
        1.5,
    )
    assert KEY.get_secret_value() not in str(failed.value)


async def test_a_call_with_no_key_fails_as_a_missing_credential_and_sends_nothing() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=RECORDED)

    with pytest.raises(ModelCallFailed) as failed:
        await reply_of(adapter(handler, key=None).stream(CALL))
    assert failed.value.kind is ErrorKind.CREDENTIAL and seen == []
    await reply_of(adapter(handler, key=None).stream(CALL, credential=SecretStr("tenant-key")))
    assert seen[0].headers["authorization"] == "Bearer tenant-key"


async def test_a_provider_that_breaks_off_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("no answer", request=request)

    with pytest.raises(ModelCallFailed) as failed:
        await reply_of(adapter(handler).stream(CALL))
    assert failed.value.kind is ErrorKind.TRANSIENT


async def test_interleaved_reasoning_replays_before_the_call_it_led_to() -> None:
    second = {**REASONING, "id": "rs_2", "encrypted_content": "gAAAA-second"}
    output = [
        REASONING,
        {
            "type": "function_call",
            "call_id": "call_a",
            "name": "add",
            "arguments": '{"a":17,"b":25}',
        },
        second,
        {
            "type": "function_call",
            "call_id": "call_b",
            "name": "add",
            "arguments": '{"a":8,"b":13}',
        },
    ]
    _, folded = await fold(sse(finished("completed", output)))
    reply = folded.reply()
    assert [t.at for t in reply.thinking] == [0, 1]
    # As a step keeps the turn: its thinking apart from its blocks.
    turn = Message(role="assistant", blocks=(*reply.thinking, *reply.blocks))
    body, _ = request_body(ModelCall(model=MODEL, messages=(ASK, turn), max_output_tokens=10))
    replayed = [
        (item.get("type"), item.get("id") or item.get("call_id")) for item in body["input"][1:]
    ]
    assert replayed == [
        ("reasoning", "rs_1"),
        ("function_call", "call_a"),
        ("reasoning", "rs_2"),
        ("function_call", "call_b"),
    ]


async def test_an_empty_credential_is_refused_and_never_runs_on_the_platforms_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=RECORDED)

    for empty in ("", "  "):
        with pytest.raises(ModelCallFailed) as failed:
            await reply_of(adapter(handler).stream(CALL, credential=SecretStr(empty)))
        assert failed.value.kind is ErrorKind.CREDENTIAL
    assert seen == []
