"""The Anthropic adapter over recorded payloads, with no network: the one
content shape both ways, what does not survive named and dropped, and each
provider error read into its kind. `anthropic_tool_use.sse` is a real
streamed response, recorded once; the error bodies are the provider's
documented ones."""

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
from acme.integrations.model_providers.anthropic import (
    AnthropicReply,
    ModelProviderAnthropicImpl,
    classify,
    request_body,
)
from acme.integrations.model_providers.calls import (
    FileData,
    Message,
    ModelCall,
    ModelReply,
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
from acme.integrations.model_providers.types import (
    Effort,
    ErrorKind,
    ProviderName,
    StopReason,
    Usage,
)
from acme.integrations.model_providers.wire import sse_events

FIXTURES = Path(__file__).parent / "fixtures" / "model_providers"
RECORDED = (FIXTURES / "anthropic_tool_use.sse").read_text()
MODEL = "claude-haiku-4-5"
KEY = SecretStr("test-key-not-a-secret")
ASK = Message(role="user", blocks=(TextBlock(text="What is 17 + 25? Use the add tool."),))


async def _lines(text: str) -> AsyncIterator[str]:
    for line in text.splitlines():
        yield line


async def fold(text: str, model: str = MODEL) -> tuple[list[StreamPart], AnthropicReply]:
    folded = AnthropicReply(model)
    parts: list[StreamPart] = []
    async for event, data in sse_events(_lines(text)):
        parts.extend(folded.feed(event, data))
    return parts, folded


def sse(*events: dict[str, Any]) -> str:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)


START = {
    "type": "message_start",
    "message": {"id": "msg_1", "model": MODEL, "usage": {"input_tokens": 10}},
}
STOP = {"type": "message_stop"}


def text_block(index: int, text: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {"type": "text", "text": ""},
        },
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "text_delta", "text": text},
        },
        {"type": "content_block_stop", "index": index},
    ]


def ended(reason: str, output: int = 5) -> dict[str, Any]:
    return {
        "type": "message_delta",
        "delta": {"stop_reason": reason},
        "usage": {"output_tokens": output},
    }


# The reply, from the provider's events.


async def test_a_recorded_tool_call_reads_as_the_one_shape() -> None:
    parts, folded = await fold(RECORDED)
    reply = folded.reply()
    assert reply.blocks == (
        ToolUseBlock(id="toolu_01H4d1xBUkGy6xHqdcuwdJse", name="add", input={"a": 17, "b": 25}),
    )
    (thought,) = reply.thinking
    assert thought.text.startswith("The user is asking me to add 17 and 25")
    assert thought.signature is not None and thought.signature.startswith("EooD")
    assert thought.source == ThinkingSource(provider=ProviderName.ANTHROPIC, model=MODEL)
    assert (reply.stop_reason, reply.truncated) == (StopReason.TOOL_USE, False)
    # The provider counts thinking inside its output; it is taken out.
    assert reply.usage == Usage(input=623, output=71, thinking=37)
    assert reply.model == "claude-haiku-4-5-20251001" and reply.dropped == ()
    assert "".join(p.text for p in parts if isinstance(p, ThinkingDelta)) == thought.text
    tool_parts = [p for p in parts if isinstance(p, ToolUseDelta)]
    assert {(p.id, p.name) for p in tool_parts} == {("toolu_01H4d1xBUkGy6xHqdcuwdJse", "add")}
    assert json.loads("".join(p.partial_input for p in tool_parts)) == {"a": 17, "b": 25}


async def test_a_block_or_a_part_the_engine_does_not_hold_is_named_and_dropped() -> None:
    events = [
        START,
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search"},
        },
        {"type": "content_block_stop", "index": 0},
        *text_block(1, "Found it."),
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "citations_delta", "citation": {"url": "https://example.test"}},
        },
        ended("end_turn"),
        STOP,
    ]
    _, folded = await fold(sse(*events))
    reply = folded.reply()
    assert reply.blocks == (TextBlock(text="Found it."),)
    assert [d.what for d in reply.dropped] == ["server_tool_use", "citations_delta"]
    assert all(d.direction == "response" for d in reply.dropped)


@pytest.mark.parametrize(
    "reason,stop",
    [
        ("end_turn", StopReason.END_TURN),
        ("stop_sequence", StopReason.END_TURN),
        ("tool_use", StopReason.TOOL_USE),
        ("pause_turn", StopReason.PAUSE),
        ("refusal", StopReason.REFUSAL),
        ("max_tokens", StopReason.OUTPUT_LIMIT),
    ],
)
async def test_each_stop_reason_is_recorded(reason: str, stop: StopReason) -> None:
    _, folded = await fold(sse(START, *text_block(0, "x"), ended(reason), STOP))
    reply = folded.reply()
    assert reply.stop_reason is stop
    assert reply.truncated is (stop is StopReason.OUTPUT_LIMIT)


async def test_an_unknown_stop_reason_is_recorded_as_truncated() -> None:
    _, folded = await fold(sse(START, *text_block(0, "x"), ended("a_new_reason"), STOP))
    reply = folded.reply()
    assert reply.stop_reason is None and reply.truncated
    assert reply.dropped[-1].what == "stop reason a_new_reason"


@pytest.mark.parametrize(
    "fragments,name",
    [
        (['{"path": "/var/lo'], "read_log"),  # cut by the output bound
        (["[1, 2]"], "read_log"),  # not an object
        (["{}"], "x" * 201),  # a name past the bound
    ],
)
async def test_a_malformed_tool_use_is_dropped_and_never_runs(
    fragments: list[str], name: str
) -> None:
    events = [
        START,
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "tool_use", "id": "toolu_9", "name": name, "input": {}},
        },
        *(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": f},
            }
            for f in fragments
        ),
        {"type": "content_block_stop", "index": 0},
        ended("max_tokens"),
        STOP,
    ]
    _, folded = await fold(sse(*events))
    reply = folded.reply()
    assert reply.blocks == () and reply.truncated
    assert reply.dropped and reply.dropped[0].what.startswith("tool use")


async def test_an_error_inside_the_stream_fails_with_its_kind_and_what_arrived() -> None:
    error = {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
    with pytest.raises(ModelCallFailed) as failed:
        await fold(sse(START, *text_block(0, "Reading the"), error))
    assert failed.value.kind is ErrorKind.OVERLOADED
    partial = failed.value.partial
    assert partial is not None and partial.truncated and partial.stop_reason is None
    assert partial.blocks == (TextBlock(text="Reading the"),)


async def test_a_stream_that_ends_early_is_broken_and_never_whole() -> None:
    _, folded = await fold(sse(START, *text_block(0, "Reading the")) + "data: {not json\n\n")
    with pytest.raises(ModelCallFailed) as failed:
        folded.reply()
    assert failed.value.kind is ErrorKind.TRANSIENT
    assert failed.value.partial is not None and failed.value.partial.truncated


# The request, from the one shape.


def continuation(model: str, thought: ThinkingBlock) -> ModelCall:
    use = ToolUseBlock(id="toolu_1", name="add", input={"a": 17, "b": 25})
    return ModelCall(
        model=model,
        system=(TextBlock(text="You are terse."),),
        messages=(
            ASK,
            Message(role="assistant", blocks=(thought, use)),
            Message(
                role="user",
                blocks=(ToolResultBlock(tool_use_id="toolu_1", parts=(TextBlock(text="42"),)),),
            ),
        ),
        max_output_tokens=1024,
    )


async def test_thinking_replays_with_its_signature_to_the_model_that_thought_it() -> None:
    _, folded = await fold(RECORDED)
    (thought,) = folded.reply().thinking
    body, dropped = request_body(continuation(MODEL, thought))
    assert dropped == ()
    assistant = body["messages"][1]["content"]
    assert assistant[0] == {
        "type": "thinking",
        "thinking": thought.text,
        "signature": thought.signature,
    }
    assert assistant[1] == {
        "type": "tool_use",
        "id": "toolu_1",
        "name": "add",
        "input": {"a": 17, "b": 25},
    }
    assert body["messages"][2]["content"] == [
        {
            "type": "tool_result",
            "tool_use_id": "toolu_1",
            "content": [{"type": "text", "text": "42"}],
            "is_error": False,
        }
    ]
    assert body["system"] == [{"type": "text", "text": "You are terse."}]


@pytest.mark.parametrize(
    "thought,reason",
    [
        (
            ThinkingBlock(
                text="t",
                signature="s",
                source=ThinkingSource(provider=ProviderName.ANTHROPIC, model="claude-sonnet-5-5"),
            ),
            "thought by",
        ),
        (
            ThinkingBlock(
                text="t",
                signature="s",
                source=ThinkingSource(provider=ProviderName.OPENAI, model=MODEL),
            ),
            "thought by",
        ),
        (
            ThinkingBlock(
                text="t", source=ThinkingSource(provider=ProviderName.ANTHROPIC, model=MODEL)
            ),
            "no signature",
        ),
    ],
)
def test_thinking_another_model_thought_or_unsigned_is_named_and_dropped(
    thought: ThinkingBlock, reason: str
) -> None:
    body, dropped = request_body(continuation(MODEL, thought))
    assert [c["type"] for c in body["messages"][1]["content"]] == ["tool_use"]
    assert len(dropped) == 1 and dropped[0].what == "thinking" and reason in dropped[0].why


def test_a_redacted_block_replays_as_its_data() -> None:
    source = ThinkingSource(provider=ProviderName.ANTHROPIC, model=MODEL)
    thought = ThinkingBlock(signature="opaque", source=source, redacted=True)
    body, _ = request_body(continuation(MODEL, thought))
    assert body["messages"][1]["content"][0] == {"type": "redacted_thinking", "data": "opaque"}


def test_cache_markers_take_the_latest_breakpoints_the_provider_allows() -> None:
    turns = tuple(
        Message(
            role="user" if i % 2 == 0 else "assistant",
            blocks=(TextBlock(text=f"turn {i}"),),
            cache=True,
        )
        for i in range(5)
    )
    body, dropped = request_body(
        ModelCall(
            model=MODEL,
            system=(TextBlock(text="kind prompt"),),
            system_cache=True,
            messages=turns,
            max_output_tokens=100,
        )
    )
    marked = [m["content"][-1].get("cache_control") for m in body["messages"]]
    # Six markers, the system's first: the latest four stay.
    assert marked == [None, *[{"type": "ephemeral"}] * 4]
    assert "cache_control" not in body["system"][-1]
    assert [d.what for d in dropped] == ["cache marker"] * 2


def test_files_render_from_the_bytes_the_call_carries() -> None:
    image, pdf, notes, sheet = uuid4(), uuid4(), uuid4(), uuid4()
    call = ModelCall(
        model=MODEL,
        messages=(
            Message(
                role="user",
                blocks=(
                    ImageBlock(attachment_id=image),
                    DocumentBlock(attachment_id=pdf),
                    DocumentBlock(attachment_id=notes),
                    DocumentBlock(attachment_id=sheet),
                ),
            ),
        ),
        max_output_tokens=100,
        files=(
            FileData(attachment_id=image, name="chart.png", media_type="image/png", data=b"png"),
            FileData(attachment_id=pdf, name="spec.pdf", media_type="application/pdf", data=b"pdf"),
            FileData(attachment_id=notes, name="notes.txt", media_type="text/plain", data=b"notes"),
            FileData(
                attachment_id=sheet, name="a.xlsx", media_type="application/vnd.ms-excel", data=b"x"
            ),
        ),
    )
    body, dropped = request_body(call)
    content = body["messages"][0]["content"]
    assert content[0] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "cG5n"},
    }
    assert content[1]["source"] == {
        "type": "base64",
        "media_type": "application/pdf",
        "data": "cGRm",
    }
    assert content[2]["source"] == {"type": "text", "media_type": "text/plain", "data": "notes"}
    assert len(content) == 3 and [d.what for d in dropped] == ["document"]
    missing = call.model_copy(update={"files": ()})
    with pytest.raises(ModelCallFailed) as failed:
        request_body(missing)
    assert failed.value.kind is ErrorKind.INVALID_REQUEST


def test_effort_a_thinking_budget_and_a_schema_map_to_the_request() -> None:
    schema = OutputSchema(name="verdict", json_schema={"type": "object"})
    call = ModelCall(
        model="claude-sonnet-5-5",
        messages=(ASK,),
        max_output_tokens=100,
        effort=Effort.HIGH,
        output_schema=schema,
    )
    body, dropped = request_body(call)
    assert body["output_config"] == {
        "effort": "high",
        "format": {"type": "json_schema", "schema": {"type": "object"}},
    }
    assert dropped == () and "thinking" not in body
    budgeted, _ = request_body(
        call.model_copy(update={"effort": None, "thinking_budget": 1024, "output_schema": None})
    )
    assert (
        budgeted["thinking"] == {"type": "enabled", "budget_tokens": 1024}
        and "output_config" not in budgeted
    )
    _, none = request_body(call.model_copy(update={"effort": Effort.NONE}))
    assert [d.what for d in none] == ["effort none"]


# The kind of an error, from its status and its message.


ERRORS = json.loads((FIXTURES / "anthropic_errors.json").read_text())


@pytest.mark.parametrize("case", ERRORS, ids=lambda c: f"{c['status']}-{c['kind']}")
def test_each_error_is_read_into_its_kind(case: dict[str, Any]) -> None:
    body = case["body"]
    raw = body if isinstance(body, str) else json.dumps(body)
    kind, message = classify(case["status"], raw.encode() if case["status"] else raw)
    assert kind is ErrorKind(case["kind"])
    assert message


def test_the_cases_cover_every_kind() -> None:
    assert {ErrorKind(c["kind"]) for c in ERRORS} == set(ErrorKind)


# The adapter over HTTP, with the provider stood in by a transport.


def adapter(
    handler: Callable[[httpx.Request], httpx.Response], key: SecretStr | None = KEY
) -> ModelProviderAnthropicImpl:
    return ModelProviderAnthropicImpl(
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        api_key=key,
        base_url="https://provider.test",
        timeout=timedelta(seconds=5),
    )


CALL = ModelCall(model=MODEL, messages=(ASK,), max_output_tokens=2048, thinking_budget=1024)


async def test_a_call_streams_the_recorded_reply_over_http() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=RECORDED, headers={"content-type": "text/event-stream"})

    reply = await reply_of(adapter(handler).stream(CALL))
    assert isinstance(reply, ModelReply) and reply.stop_reason is StopReason.TOOL_USE
    (request,) = seen
    assert request.url == "https://provider.test/v1/messages"
    assert request.headers["x-api-key"] == KEY.get_secret_value()
    assert json.loads(request.content) == request_body(CALL)[0]


async def test_a_call_runs_on_its_own_credential_when_it_carries_one() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["x-api-key"])
        return httpx.Response(200, text=RECORDED)

    await reply_of(adapter(handler, key=None).stream(CALL, credential=SecretStr("tenant-key")))
    assert seen == ["tenant-key"]
    with pytest.raises(ModelCallFailed) as failed:
        await reply_of(adapter(handler, key=None).stream(CALL))
    assert failed.value.kind is ErrorKind.CREDENTIAL and len(seen) == 1


async def test_a_refusal_carries_its_kind_its_status_and_the_providers_wait() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = {"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}}
        return httpx.Response(429, json=body, headers={"retry-after": "7"})

    with pytest.raises(ModelCallFailed) as failed:
        await reply_of(adapter(handler).stream(CALL))
    assert (failed.value.kind, failed.value.status, failed.value.retry_after) == (
        ErrorKind.RATE_LIMITED,
        429,
        7.0,
    )
    assert KEY.get_secret_value() not in str(failed.value)


async def test_a_provider_that_does_not_answer_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ModelCallFailed) as failed:
        await reply_of(adapter(handler).stream(CALL))
    assert failed.value.kind is ErrorKind.TRANSIENT


# A turn whose thinking sits between its blocks.


INTERLEAVED = (FIXTURES / "anthropic_interleaved.sse").read_text()


def streamed_turn(text: str) -> list[dict[str, Any]]:
    """The turn as the stream carried it, block by block, in its order."""
    blocks: dict[int, dict[str, Any]] = {}
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        event = json.loads(line[6:])
        if event["type"] == "content_block_start":
            blocks[event["index"]] = dict(event["content_block"])
        elif event["type"] == "content_block_delta":
            block, delta = blocks[event["index"]], event["delta"]
            if delta["type"] == "thinking_delta":
                block["thinking"] += delta["thinking"]
            elif delta["type"] == "signature_delta":
                block["signature"] += delta["signature"]
            elif delta["type"] == "text_delta":
                block["text"] += delta["text"]
            elif delta["type"] == "input_json_delta":
                block["json"] = block.get("json", "") + delta["partial_json"]
    for block in blocks.values():
        if block["type"] == "tool_use":
            block["input"] = json.loads(block.pop("json"))
    return [blocks[i] for i in sorted(blocks)]


async def test_an_interleaved_turn_replays_in_the_providers_order_byte_for_byte() -> None:
    _, folded = await fold(INTERLEAVED)
    reply = folded.reply()
    assert [b.kind for b in reply.turn()] == [
        "thinking",
        "text",
        "thinking",
        "tool_use",
        "thinking",
        "tool_use",
    ]
    assert [t.at for t in reply.thinking] == [0, 1, 2]
    results = Message(
        role="user",
        blocks=(
            ToolResultBlock(tool_use_id="toolu_01FirstPairAdd", parts=(TextBlock(text="42"),)),
            ToolResultBlock(tool_use_id="toolu_01SecondPairAdd", parts=(TextBlock(text="21"),)),
        ),
    )
    expected = json.dumps(streamed_turn(INTERLEAVED))
    # As the reply hands it on, and as a step keeps it: its thinking apart.
    for blocks in (reply.turn(), (*reply.thinking, *reply.blocks)):
        call = ModelCall(
            model=MODEL,
            messages=(ASK, Message(role="assistant", blocks=blocks), results),
            max_output_tokens=2048,
            thinking_budget=1024,
        )
        body, dropped = request_body(call)
        assert dropped == ()
        assert json.dumps(body["messages"][1]["content"]) == expected


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
