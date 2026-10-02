"""The OpenAI adapter: the Responses API over HTTP, streamed, stored nowhere
at the provider (`store: false`), with no retries of its own.

A reasoning model's thinking comes back as an encrypted item, which this
adapter keeps as the thinking block's signature and replays to the model
that thought it. The provider caches a prompt's prefix on its own, so a
cache marker is named and dropped.

As in the Anthropic adapter, the translation is pure: `request_body`,
`OpenAIReply`, and `classify`, which its tests run over recorded payloads."""

import base64
import json
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
from pydantic import SecretStr, ValidationError

from acme.infra.base import thaw_mapping
from acme.integrations.model_providers import ModelProviderInterface
from acme.integrations.model_providers.calls import (
    Finished,
    Message,
    ModelCall,
    ModelReply,
    StreamPart,
    TextDelta,
    ThinkingDelta,
    ToolUseDelta,
)
from acme.integrations.model_providers.content import (
    MAX_NAME,
    UNPARSED,
    DocumentBlock,
    ImageBlock,
    ReplyBlock,
    TextBlock,
    ThinkingBlock,
    ThinkingSource,
    ToolResultBlock,
    ToolUseBlock,
    in_turn_order,
)
from acme.integrations.model_providers.failures import ModelCallFailed
from acme.integrations.model_providers.types import (
    Dropped,
    Effort,
    ErrorKind,
    ProviderName,
    StopReason,
    Usage,
)
from acme.integrations.model_providers.wire import (
    clipped,
    field,
    json_object,
    key_for,
    retry_after,
    sse_events,
)

INCOMPLETE: dict[str, StopReason] = {
    "max_output_tokens": StopReason.OUTPUT_LIMIT,
    "content_filter": StopReason.CONTENT_FILTER,
}
"""Why a response ended incomplete. A reason the provider adds later maps to
none, so its reply is recorded as truncated."""


def _dropped(what: str, why: str) -> Dropped:
    return Dropped(direction="request", what=what, why=why)


def _data_url(call: ModelCall, block: ImageBlock | DocumentBlock) -> tuple[str, str]:
    found = call.file(block.attachment_id)
    if found is None:
        raise ModelCallFailed(
            ErrorKind.INVALID_REQUEST, f"attachment {block.attachment_id} has no bytes in the call"
        )
    encoded = base64.b64encode(found.data).decode("ascii")
    return found.name, f"data:{found.media_type};base64,{encoded}"


def _user_part(call: ModelCall, block: TextBlock | ImageBlock | DocumentBlock) -> dict[str, Any]:
    if isinstance(block, TextBlock):
        return {"type": "input_text", "text": block.text}
    name, url = _data_url(call, block)
    if isinstance(block, ImageBlock):
        return {"type": "input_image", "image_url": url}
    return {"type": "input_file", "filename": name, "file_data": url}


def _tool_output(
    call: ModelCall, result: ToolResultBlock, dropped: list[Dropped]
) -> dict[str, Any]:
    """A tool's result: its text as one string, or, when it holds an image or
    a document, each part in its order, a file as data, as a user turn
    carries one."""
    output: str | list[dict[str, Any]]
    if all(isinstance(part, TextBlock) for part in result.parts):
        output = "\n".join(part.text for part in result.parts if isinstance(part, TextBlock))
    else:
        output = [
            _user_part(call, part)
            for part in result.parts
            if not (isinstance(part, TextBlock) and not part.text)
        ]
    if result.is_error:
        dropped.append(
            _dropped("a tool result's error flag", "the provider has none; its text says it")
        )
    return {"type": "function_call_output", "call_id": result.tool_use_id, "output": output}


def _user_items(call: ModelCall, message: Message, dropped: list[Dropped]) -> list[dict[str, Any]]:
    """A user turn: the tool results it answers, then what it says."""
    items: list[dict[str, Any]] = []
    parts: list[dict[str, Any]] = []
    for block in message.blocks:
        if isinstance(block, ToolResultBlock):
            items.append(_tool_output(call, block, dropped))
        elif isinstance(block, ThinkingBlock | ToolUseBlock):
            dropped.append(_dropped(block.kind.replace("_", " "), "a user turn carries none"))
        else:
            parts.append(_user_part(call, block))
    if parts:
        items.append({"role": "user", "content": parts})
    return items


def _assistant_items(
    call: ModelCall, message: Message, dropped: list[Dropped]
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    # Each reasoning item replays in the place its turn gave it, before the
    # call it led to.
    for block in in_turn_order(message.blocks):
        if isinstance(block, ThinkingBlock):
            if not block.replays_to(ProviderName.OPENAI, call.model):
                source = "no signature" if block.signature is None else f"thought by {block.source}"
                dropped.append(
                    _dropped("thinking", f"it replays only to the model that thought it ({source})")
                )
                continue
            item: dict[str, Any] = {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": block.text}] if block.text else [],
                "encrypted_content": block.signature,
            }
            if block.ref is not None:
                item["id"] = block.ref
            items.append(item)
        elif isinstance(block, TextBlock):
            if block.text:
                items.append({"role": "assistant", "content": block.text})
        elif isinstance(block, ToolUseBlock):
            items.append(
                {
                    "type": "function_call",
                    "call_id": block.id,
                    "name": block.name,
                    "arguments": json.dumps(thaw_mapping(block.input), separators=(",", ":")),
                }
            )
        else:
            dropped.append(_dropped(block.kind.replace("_", " "), "an assistant turn carries none"))
    return items


def request_body(call: ModelCall) -> tuple[dict[str, Any], tuple[Dropped, ...]]:
    """The call as the provider's request, and what did not survive it."""
    dropped: list[Dropped] = []
    items: list[dict[str, Any]] = []
    for message in call.messages:
        if message.role == "user":
            items.extend(_user_items(call, message, dropped))
        else:
            items.extend(_assistant_items(call, message, dropped))
    markers = int(call.system_cache) + sum(1 for m in call.messages if m.cache)
    if markers:
        dropped.append(
            _dropped(f"{markers} cache marker(s)", "the provider caches a prefix on its own")
        )
    body: dict[str, Any] = {
        "model": call.model,
        "input": items,
        "max_output_tokens": call.max_output_tokens,
        "stream": True,
        "store": False,
    }
    instructions = "\n\n".join(block.text for block in call.system if block.text)
    if instructions:
        body["instructions"] = instructions
    if call.tools:
        body["tools"] = [
            {
                "type": "function",
                "name": t.name,
                "description": t.description,
                "parameters": thaw_mapping(t.input_schema),
                "strict": False,
            }
            for t in call.tools
        ]
    if call.effort is not None:
        body["reasoning"] = {"effort": call.effort.value}
        if call.effort is not Effort.NONE:
            body["reasoning"]["summary"] = "auto"
            # Stored nowhere, a reasoning item comes back encrypted, so it can
            # be replayed to the model that thought it.
            body["include"] = ["reasoning.encrypted_content"]
    if call.thinking_budget is not None:
        dropped.append(_dropped("a thinking budget", "the provider sets reasoning by effort alone"))
    if call.output_schema is not None:
        body["text"] = {
            "format": {
                "type": "json_schema",
                "name": call.output_schema.name,
                "schema": thaw_mapping(call.output_schema.json_schema),
                "strict": False,
            }
        }
    return body, tuple(dropped)


SPEND_CODES = frozenset(
    {
        "insufficient_quota",
        "credit_balance_exhausted",
        "organization_spend_limit_exceeded",
        "project_spend_limit_exceeded",
        "organization_usage_limit_exceeded",
    }
)
"""The codes of a spent credit, quota, or spend limit, which the provider
answers with the status of a rate limit and which no wait clears."""

SPEND_WORDS = (
    "exceeded your current quota",
    "credit balance exhausted",
    "spend limit reached",
    "usage limit reached",
)


def classify(status: int | None, body: str | bytes | dict[str, Any]) -> tuple[ErrorKind, str]:
    """A provider error's kind, from its status and its message: the
    provider answers a spent quota with the status of a rate limit, so the
    code and the message are read before the status. `status` is None for
    an error the stream itself carried, whose body is the error object."""
    decoded = body if isinstance(body, dict) else json_object(body)
    error = decoded.get("error") if isinstance(decoded.get("error"), dict) else decoded
    code = str(field(error, "code") or "")
    kind_of = str(field(error, "type") or "")
    message = str(field(error, "message") or "")
    if not message and not isinstance(body, dict):
        message = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    text = message.lower()
    said = f"{status or 'stream'} {code or kind_of or 'error'}: {clipped(message)}"
    if code == "context_length_exceeded" or any(
        s in text for s in ("context length", "context window", "too many tokens")
    ):
        return ErrorKind.CONTEXT_OVERFLOW, said
    if (
        code in SPEND_CODES
        or kind_of == "insufficient_quota"
        or any(s in text for s in SPEND_WORDS)
    ):
        return ErrorKind.BILLING, said
    if code in ("invalid_api_key", "unsupported_country_region_territory") or status in (401, 403):
        return ErrorKind.CREDENTIAL, said
    if code == "model_not_found" or (status == 404 and "model" in text):
        return ErrorKind.MODEL_UNAVAILABLE, said
    if code == "rate_limit_exceeded" or status == 429:
        return ErrorKind.RATE_LIMITED, said
    if code == "server_is_overloaded" or status == 503 or "overloaded" in text:
        return ErrorKind.OVERLOADED, said
    if code == "server_error" or status in (408, 409, 500, 502, 504):
        return ErrorKind.TRANSIENT, said
    if code == "invalid_prompt" or "usage policy" in text:
        return ErrorKind.PERMANENT, said
    if kind_of == "invalid_request_error" or status in (400, 413, 422):
        return ErrorKind.INVALID_REQUEST, said
    if status is None or status >= 500:
        return ErrorKind.TRANSIENT, said
    return ErrorKind.PERMANENT, said


class OpenAIReply:
    """The provider's events folded into stream parts and one reply. The
    reply is built from the finished response the last event carries; the
    items the stream finished before it broke make the partial."""

    def __init__(self, model: str, dropped: tuple[Dropped, ...] = ()) -> None:
        self._model = model
        self._reported = model
        self._id: str | None = None
        self._calls: dict[int, tuple[str, str]] = {}  # output index -> (call id, name)
        self._done: dict[int, dict[str, Any]] = {}
        self._text: dict[int, list[str]] = {}
        self._final: dict[str, Any] | None = None
        self._request_dropped = dropped
        self._dropped: list[Dropped] = []

    def _drop(self, what: str, why: str) -> None:
        self._dropped.append(Dropped(direction="response", what=what, why=why))

    def _note(self, response: Any) -> None:
        if isinstance(field(response, "id"), str):
            self._id = field(response, "id")
        if isinstance(field(response, "model"), str) and field(response, "model"):
            self._reported = field(response, "model")

    def feed(self, event: str, data: str) -> list[StreamPart]:
        payload = json_object(data)
        kind = str(payload.get("type") or event)
        index = payload.get("output_index")
        at = index if isinstance(index, int) and index >= 0 else None
        if kind in ("response.created", "response.in_progress"):
            self._note(payload.get("response"))
        elif kind == "response.output_item.added" and at is not None:
            item = payload.get("item")
            if field(item, "type") == "function_call":
                call_id, name = str(field(item, "call_id") or ""), str(field(item, "name") or "")
                if 0 < len(call_id) <= MAX_NAME and 0 < len(name) <= MAX_NAME:
                    self._calls[at] = (call_id, name)
                    return [ToolUseDelta(index=at, id=call_id, name=name)]
        elif kind == "response.output_text.delta" and at is not None:
            text = str(payload.get("delta") or "")
            self._text.setdefault(at, []).append(text)
            return [TextDelta(index=at, text=text)]
        elif kind == "response.reasoning_summary_text.delta" and at is not None:
            return [ThinkingDelta(index=at, text=str(payload.get("delta") or ""))]
        elif kind == "response.function_call_arguments.delta" and at in self._calls:
            call_id, name = self._calls[at]
            return [
                ToolUseDelta(
                    index=at, id=call_id, name=name, partial_input=str(payload.get("delta") or "")
                )
            ]
        elif kind == "response.output_item.done" and at is not None:
            item = payload.get("item")
            if isinstance(item, dict):
                self._done[at] = item
        elif kind in ("response.completed", "response.incomplete"):
            response = payload.get("response")
            if isinstance(response, dict):
                self._note(response)
                self._final = response
        elif kind in ("response.failed", "error"):
            error = field(payload, "response", "error") if kind == "response.failed" else payload
            error_kind, message = classify(None, error if isinstance(error, dict) else {})
            raise ModelCallFailed(error_kind, message, partial=self.partial())
        return []

    def _items_out(
        self, items: list[Any], *, finished: bool
    ) -> tuple[list[ReplyBlock], list[ThinkingBlock], bool, bool]:
        """The blocks and the thinking of the response's items, whether a tool
        was called, and whether the model refused. `finished` is whether the
        response completed, which its calls did with it."""
        blocks: list[ReplyBlock] = []
        thinking: list[ThinkingBlock] = []
        called = refused = False
        source = ThinkingSource(provider=ProviderName.OPENAI, model=self._model)
        for item in items:
            kind = field(item, "type")
            if kind == "message":
                for part in field(item, "content") or []:
                    part_kind = field(part, "type")
                    if part_kind == "output_text" and field(part, "text"):
                        blocks.append(TextBlock(text=str(field(part, "text"))))
                    elif part_kind == "refusal":
                        refused = True
                        blocks.append(TextBlock(text=str(field(part, "refusal") or "")))
                    else:
                        self._drop(f"message part {part_kind}", "the engine holds no such part")
            elif kind == "function_call":
                tool_use = self._tool_use(item, finished=finished)
                if tool_use is not None:
                    called = True
                    blocks.append(tool_use)
            elif kind == "reasoning":
                summary = [str(field(s, "text") or "") for s in field(item, "summary") or []]
                encrypted = field(item, "encrypted_content")
                ref = field(item, "id")
                thinking.append(
                    ThinkingBlock(
                        text="\n\n".join(s for s in summary if s),
                        signature=encrypted if isinstance(encrypted, str) and encrypted else None,
                        source=source,
                        ref=ref if isinstance(ref, str) and len(ref) <= MAX_NAME else None,
                        at=len(blocks),
                    )
                )
            else:
                self._drop(f"output item {kind}", "the engine holds no such item")
        return blocks, thinking, called, refused

    def _tool_use(self, item: Any, *, finished: bool) -> ToolUseBlock | None:
        """A call's block. Arguments that are not a JSON object are kept as
        the model wrote them when the response completed, so the call is
        refused as invalid input the model reads; in a response that did
        not, they are dropped, and the call never runs either way."""
        call_id = str(field(item, "call_id") or "")
        raw = str(field(item, "arguments") or "")
        try:
            value = json.loads(raw or "{}")
        except ValueError:
            value = None
        if not isinstance(value, dict):
            if not finished:
                self._drop(f"tool use {call_id}", "its input is no whole object, so it never runs")
                return None
            value = {UNPARSED: raw}
        try:
            return ToolUseBlock(id=call_id, name=str(field(item, "name") or ""), input=value)
        except ValidationError:
            self._drop(f"tool use {call_id}", "it does not fit the block")
            return None

    @staticmethod
    def _usage_out(usage: Any) -> Usage:
        def number(*path: str) -> int:
            value = field(usage, *path)
            return (
                value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0
            )

        cached = number("input_tokens_details", "cached_tokens")
        written = number("input_tokens_details", "cache_write_tokens")
        reasoning = number("output_tokens_details", "reasoning_tokens")
        # The provider counts cached and cache-written tokens inside its
        # input, and reasoning inside its output; each is taken out, so no
        # class holds another.
        return Usage(
            input=max(0, number("input_tokens") - cached - written),
            cache_read=cached,
            cache_write=written,
            output=max(0, number("output_tokens") - reasoning),
            thinking=reasoning,
        )

    def _reply(self, items: list[Any], usage: Any, stop: StopReason | None) -> ModelReply:
        blocks, thinking, called, refused = self._items_out(
            items, finished=stop is StopReason.END_TURN
        )
        if stop is StopReason.END_TURN:
            stop = StopReason.TOOL_USE if called else StopReason.REFUSAL if refused else stop
        return ModelReply(
            blocks=tuple(blocks),
            thinking=tuple(thinking),
            stop_reason=stop,
            truncated=stop is None or stop is StopReason.OUTPUT_LIMIT,
            usage=self._usage_out(usage),
            model=self._reported[:MAX_NAME],
            response_id=self._id[:MAX_NAME] if self._id else None,
            dropped=self._request_dropped + tuple(self._dropped),
        )

    def partial(self) -> ModelReply:
        """What arrived so far, truncated: the finished items, and the text of
        an item the stream broke inside."""
        items: list[Any] = [self._done[i] for i in sorted(self._done)]
        for at, texts in sorted(self._text.items()):
            if at not in self._done and "".join(texts):
                items.append(
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "".join(texts)}],
                    }
                )
        return self._reply(items, None, None)

    def reply(self) -> ModelReply:
        final = self._final
        if final is None:
            raise ModelCallFailed(
                ErrorKind.TRANSIENT,
                "the stream ended before the response did",
                partial=self.partial(),
            )
        status = field(final, "status")
        if status == "completed":
            stop: StopReason | None = StopReason.END_TURN
        else:
            reason = str(field(final, "incomplete_details", "reason") or "")
            stop = INCOMPLETE.get(reason)
            if stop is None:
                self._drop(
                    f"status {status} ({reason or 'no reason'})",
                    "unknown, so the reply is truncated",
                )
        output = field(final, "output")
        return self._reply(output if isinstance(output, list) else [], field(final, "usage"), stop)


class ModelProviderOpenAIImpl(ModelProviderInterface):
    def __init__(
        self,
        *,
        http: httpx.AsyncClient,
        api_key: SecretStr | None,
        base_url: str,
        timeout: timedelta,
    ) -> None:
        self._http = http
        self._api_key = api_key
        self._url = base_url.rstrip("/") + "/v1/responses"
        self._timeout = httpx.Timeout(timeout.total_seconds())

    @property
    def provider(self) -> ProviderName:
        return ProviderName.OPENAI

    async def stream(
        self, call: ModelCall, *, credential: SecretStr | None = None
    ) -> AsyncIterator[StreamPart]:
        key = key_for("OpenAI", credential, self._api_key)
        body, dropped = request_body(call)
        folded = OpenAIReply(call.model, dropped)
        headers = {"authorization": f"Bearer {key.get_secret_value()}"}
        try:
            async with self._http.stream(
                "POST", self._url, json=body, headers=headers, timeout=self._timeout
            ) as response:
                if response.status_code != 200:
                    raw = await response.aread()
                    kind, message = classify(response.status_code, raw)
                    raise ModelCallFailed(
                        kind,
                        message,
                        status=response.status_code,
                        retry_after=retry_after(response.headers),
                    )
                async for event, data in sse_events(response.aiter_lines()):
                    for part in folded.feed(event, data):
                        yield part
        except httpx.HTTPError as error:
            raise ModelCallFailed(
                ErrorKind.TRANSIENT,
                f"OpenAI did not answer: {type(error).__name__}",
                partial=folded.partial(),
            ) from None
        yield Finished(reply=folded.reply())

    def describe(self) -> str:
        configured = "a platform key" if self._api_key is not None else "no platform key"
        return f"model provider openai: the Responses API, {configured}"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        await self._http.aclose()
