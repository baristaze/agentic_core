"""The Anthropic adapter: the Messages API over HTTP, streamed, with no
retries of its own.

The translation is pure and lives in three pieces this module's tests run
over recorded payloads: `request_body` (the one content shape to the
provider's request), `AnthropicReply` (the provider's events to stream
parts and a reply), and `classify` (the provider's error to its kind)."""

import base64
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
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
    Block,
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
    json_object,
    key_for,
    retry_after,
    sse_events,
)
from acme.integrations.model_providers.wire import (
    field as get,
)

API_VERSION = "2023-06-01"
MAX_BREAKPOINTS = 4
"""The provider's limit on cache breakpoints in one request."""

STOP_REASONS: dict[str, StopReason] = {
    "end_turn": StopReason.END_TURN,
    "stop_sequence": StopReason.END_TURN,
    "tool_use": StopReason.TOOL_USE,
    "max_tokens": StopReason.OUTPUT_LIMIT,
    "model_context_window_exceeded": StopReason.OUTPUT_LIMIT,
    "pause_turn": StopReason.PAUSE,
    "refusal": StopReason.REFUSAL,
}
"""The provider's stop reasons. One it adds later maps to none, so its reply
is recorded as truncated, never assumed whole."""

CACHE = {"type": "ephemeral"}


def _dropped(what: str, why: str) -> Dropped:
    return Dropped(direction="request", what=what, why=why)


def _file_source(call: ModelCall, attachment_id: Any) -> tuple[str, str]:
    found = call.file(attachment_id)
    if found is None:
        raise ModelCallFailed(
            ErrorKind.INVALID_REQUEST, f"attachment {attachment_id} has no bytes in the call"
        )
    return found.media_type, base64.b64encode(found.data).decode("ascii")


def _part(call: ModelCall, block: TextBlock | ImageBlock | DocumentBlock) -> dict[str, Any] | None:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text} if block.text else None
    media_type, data = _file_source(call, block.attachment_id)
    if isinstance(block, ImageBlock):
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data},
        }
    if media_type == "application/pdf":
        return {
            "type": "document",
            "source": {"type": "base64", "media_type": media_type, "data": data},
        }
    if media_type.startswith("text/"):
        text = base64.b64decode(data).decode("utf-8", errors="replace")
        return {
            "type": "document",
            "source": {"type": "text", "media_type": "text/plain", "data": text},
        }
    return None


def _block(
    call: ModelCall, message: Message, block: Block, dropped: list[Dropped]
) -> dict[str, Any] | None:
    """One block of a message as the provider reads it, or None with what was
    dropped named."""
    assistant = message.role == "assistant"
    if isinstance(block, ThinkingBlock):
        if not assistant:
            dropped.append(_dropped("thinking", "a user turn carries no thinking"))
            return None
        if not block.replays_to(ProviderName.ANTHROPIC, call.model):
            source = "no signature" if block.signature is None else f"thought by {block.source}"
            dropped.append(
                _dropped("thinking", f"it replays only to the model that thought it ({source})")
            )
            return None
        if block.redacted:
            return {"type": "redacted_thinking", "data": block.signature}
        return {"type": "thinking", "thinking": block.text, "signature": block.signature}
    if isinstance(block, ToolUseBlock):
        if not assistant:
            dropped.append(_dropped("tool use", "only an assistant turn calls a tool"))
            return None
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": thaw_mapping(block.input),
        }
    if isinstance(block, ToolResultBlock):
        if assistant:
            dropped.append(_dropped("tool result", "only a user turn answers a tool"))
            return None
        parts: list[dict[str, Any]] = []
        for part in block.parts:
            translated = _part(call, part)
            if translated is None:
                dropped.append(
                    _dropped(f"{part.kind} in a tool result", "the provider reads no such part")
                )
            else:
                parts.append(translated)
        return {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
            "content": parts,
            "is_error": block.is_error,
        }
    part = _part(call, block)
    if part is None:
        what = "empty text" if isinstance(block, TextBlock) else f"{block.kind}"
        dropped.append(_dropped(what, "the provider reads no such block"))
    return part


def request_body(call: ModelCall) -> tuple[dict[str, Any], tuple[Dropped, ...]]:
    """The call as the provider's request, and what did not survive it."""
    dropped: list[Dropped] = []
    body: dict[str, Any] = {
        "model": call.model,
        "max_tokens": call.max_output_tokens,
        "stream": True,
    }
    marked: list[dict[str, Any]] = []
    if call.system:
        system = [{"type": "text", "text": block.text} for block in call.system if block.text]
        if system:
            body["system"] = system
            if call.system_cache:
                marked.append(system[-1])
    messages: list[dict[str, Any]] = []
    for message in call.messages:
        # The provider refuses a latest turn whose thinking moved: each block
        # replays in the place its turn gave it.
        ordered = in_turn_order(message.blocks)
        content = [c for c in (_block(call, message, b, dropped) for b in ordered) if c is not None]
        if not content:
            dropped.append(_dropped(f"a {message.role} turn", "nothing in it survived"))
            continue
        if message.cache:
            marked.append(content[-1])
        messages.append({"role": message.role, "content": content})
    body["messages"] = messages
    for _ in marked[: max(0, len(marked) - MAX_BREAKPOINTS)]:
        dropped.append(_dropped("cache marker", f"the provider takes {MAX_BREAKPOINTS} at most"))
    for block in marked[-MAX_BREAKPOINTS:]:
        block["cache_control"] = CACHE
    if call.tools:
        body["tools"] = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": thaw_mapping(t.input_schema),
            }
            for t in call.tools
        ]
    if call.thinking_budget is not None:
        body["thinking"] = {"type": "enabled", "budget_tokens": call.thinking_budget}
    output: dict[str, Any] = {}
    if call.effort is Effort.NONE:
        dropped.append(_dropped("effort none", "the provider has no level below low"))
    elif call.effort is not None:
        output["effort"] = call.effort.value
    if call.output_schema is not None:
        output["format"] = {
            "type": "json_schema",
            "schema": thaw_mapping(call.output_schema.json_schema),
        }
    if output:
        body["output_config"] = output
    return body, tuple(dropped)


def unauthenticated(body: str | bytes | dict[str, Any]) -> bool:
    """Whether the error is Anthropic's own authentication error: the key
    itself was not taken, as against a permission it lacks."""
    decoded = body if isinstance(body, dict) else json_object(body)
    return get(decoded, "error", "type") == "authentication_error"


def classify(status: int | None, body: str | bytes | dict[str, Any]) -> tuple[ErrorKind, str]:
    """A provider error's kind, from its status and its message: the
    provider answers a spent balance as an invalid request, and an overlong
    prompt as one too, so the message is read before the status. `status`
    is None for an error the stream itself carried."""
    decoded = body if isinstance(body, dict) else json_object(body)
    kind_of = str(get(decoded, "error", "type") or "")
    code = str(get(decoded, "error", "details", "error_code") or "")
    message = str(get(decoded, "error", "message") or "")
    if not message and not isinstance(body, dict):
        message = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    text = message.lower()
    said = f"{status or 'stream'} {kind_of or 'error'}: {clipped(message)}"
    if kind_of == "request_too_large" or any(
        s in text
        for s in ("prompt is too long", "context window", "context limit", "too many tokens")
    ):
        return ErrorKind.CONTEXT_OVERFLOW, said
    # A spend cap answers as a rate limit, and a spend limit an account set
    # as an invalid request; neither clears by waiting.
    if (
        kind_of == "billing_error"
        or status == 402
        or code == "enforced_spend_limit_reached"
        or any(s in text for s in ("credit balance", "api usage limits"))
    ):
        return ErrorKind.BILLING, said
    if kind_of in ("authentication_error", "permission_error") or status in (401, 403):
        return ErrorKind.CREDENTIAL, said
    if kind_of == "not_found_error" or status == 404:
        if text.startswith("model:") or "model" in text:
            return ErrorKind.MODEL_UNAVAILABLE, said
        return ErrorKind.PERMANENT, said
    if kind_of == "rate_limit_error" or status == 429:
        return ErrorKind.RATE_LIMITED, said
    if kind_of == "overloaded_error" or status == 529:
        return ErrorKind.OVERLOADED, said
    if kind_of in ("api_error", "timeout_error") or status in (408, 409, 500, 502, 503, 504):
        return ErrorKind.TRANSIENT, said
    if kind_of == "invalid_request_error" or status in (400, 413, 422):
        if "usage policy" in text or "content filtering" in text:
            return ErrorKind.PERMANENT, said
        return ErrorKind.INVALID_REQUEST, said
    if status is None or status >= 500:
        return ErrorKind.TRANSIENT, said
    return ErrorKind.PERMANENT, said


@dataclass
class _Open:
    """A block the stream is still writing."""

    type: str
    text: list[str] = field(default_factory=list)
    signature: list[str] = field(default_factory=list)
    id: str = ""
    name: str = ""
    json: list[str] = field(default_factory=list)
    dropped: bool = False


class AnthropicReply:
    """The provider's events folded into stream parts and one reply. Feed it
    each event; it never hands a provider payload on, and a block it does
    not know is named and dropped."""

    def __init__(self, model: str, dropped: tuple[Dropped, ...] = ()) -> None:
        self._model = model
        self._reported = model
        self._id: str | None = None
        self._usage: dict[str, Any] = {}
        self._blocks: dict[int, _Open] = {}
        self._stop: str | None = None
        self._finished = False
        self._dropped = list(dropped)

    def _drop(self, what: str, why: str) -> None:
        self._dropped.append(Dropped(direction="response", what=what, why=why))

    def _count(self, usage: Any) -> None:
        """The usage an event reports; a later count of a class replaces an
        earlier one, as the last event's counts are the call's."""
        if isinstance(usage, dict):
            self._usage.update(usage)

    def feed(self, event: str, data: str) -> list[StreamPart]:
        """The parts one event yields. An error event is `ModelCallFailed`
        with what arrived as its partial."""
        payload = json_object(data)
        kind = str(payload.get("type") or event)
        if kind == "message_start":
            message = payload.get("message")
            self._id = get(message, "id") if isinstance(get(message, "id"), str) else None
            reported = get(message, "model")
            self._reported = reported if isinstance(reported, str) and reported else self._model
            self._count(get(message, "usage"))
        elif kind == "content_block_start":
            return self._start(payload)
        elif kind == "content_block_delta":
            return self._delta(payload)
        elif kind == "message_delta":
            stop = get(payload, "delta", "stop_reason")
            self._stop = stop if isinstance(stop, str) else self._stop
            self._count(payload.get("usage"))
        elif kind == "message_stop":
            self._finished = True
        elif kind == "error":
            error_kind, message = classify(None, payload)
            raise ModelCallFailed(
                error_kind,
                message,
                partial=self.partial(),
                authentication=unauthenticated(payload),
            )
        return []

    def _start(self, payload: dict[str, Any]) -> list[StreamPart]:
        index, block = payload.get("index"), payload.get("content_block")
        if not isinstance(index, int) or not isinstance(block, dict):
            return []
        kind = str(block.get("type"))
        opened = _Open(type=kind)
        self._blocks[index] = opened
        parts: list[StreamPart] = []
        if kind == "text":
            opened.text.append(str(block.get("text") or ""))
        elif kind == "thinking":
            opened.text.append(str(block.get("thinking") or ""))
            opened.signature.append(str(block.get("signature") or ""))
        elif kind == "redacted_thinking":
            opened.signature.append(str(block.get("data") or ""))
        elif kind == "tool_use":
            opened.id, opened.name = str(block.get("id") or ""), str(block.get("name") or "")
            if not (0 < len(opened.id) <= MAX_NAME and 0 < len(opened.name) <= MAX_NAME):
                opened.dropped = True
                self._drop("tool use", "its id or its name is empty or past the bound")
            else:
                parts.append(ToolUseDelta(index=index, id=opened.id, name=opened.name))
        else:
            opened.dropped = True
            self._drop(kind, "the engine holds no such block")
        return parts

    def _delta(self, payload: dict[str, Any]) -> list[StreamPart]:
        index, delta = payload.get("index"), payload.get("delta")
        opened = self._blocks.get(index) if isinstance(index, int) else None
        if (
            opened is None
            or opened.dropped
            or not isinstance(delta, dict)
            or not isinstance(index, int)
        ):
            return []
        kind = delta.get("type")
        if kind == "text_delta" and opened.type == "text":
            text = str(delta.get("text") or "")
            opened.text.append(text)
            return [TextDelta(index=index, text=text)]
        if kind == "thinking_delta" and opened.type == "thinking":
            text = str(delta.get("thinking") or "")
            opened.text.append(text)
            return [ThinkingDelta(index=index, text=text)]
        if kind == "signature_delta" and opened.type == "thinking":
            opened.signature.append(str(delta.get("signature") or ""))
            return []
        if kind == "input_json_delta" and opened.type == "tool_use":
            text = str(delta.get("partial_json") or "")
            opened.json.append(text)
            return [ToolUseDelta(index=index, id=opened.id, name=opened.name, partial_input=text)]
        self._drop(str(kind), "the engine holds no such part")
        return []

    def _blocks_out(self, *, finished: bool) -> tuple[list[ReplyBlock], list[ThinkingBlock]]:
        blocks: list[ReplyBlock] = []
        thinking: list[ThinkingBlock] = []
        source = ThinkingSource(provider=ProviderName.ANTHROPIC, model=self._model)
        for index in sorted(self._blocks):
            opened = self._blocks[index]
            if opened.dropped:
                continue
            if opened.type == "text":
                text = "".join(opened.text)
                if text:
                    blocks.append(TextBlock(text=text))
            elif opened.type in ("thinking", "redacted_thinking"):
                signature = "".join(opened.signature) or None
                thinking.append(
                    ThinkingBlock(
                        text="".join(opened.text),
                        signature=signature,
                        source=source,
                        redacted=opened.type == "redacted_thinking",
                        at=len(blocks),
                    )
                )
            elif opened.type == "tool_use":
                tool_use = self._tool_use(opened, finished=finished)
                if tool_use is not None:
                    blocks.append(tool_use)
        return blocks, thinking

    def _tool_use(self, opened: _Open, *, finished: bool) -> ToolUseBlock | None:
        """A tool use's block. An input that is not a JSON object is kept as
        the model wrote it when the reply stopped for its calls, so the call
        is refused as invalid input the model reads; in a reply that was cut
        it is dropped, and never runs either way."""
        raw = "".join(opened.json)
        try:
            value = json.loads(raw) if raw else {}
        except ValueError:
            value = None
        if not isinstance(value, dict):
            if not finished:
                self._drop(
                    f"tool use {opened.id}", "its input is no whole object, so it never runs"
                )
                return None
            value = {UNPARSED: raw}
        try:
            return ToolUseBlock(id=opened.id, name=opened.name, input=value)
        except ValidationError:
            self._drop(f"tool use {opened.id}", "it does not fit the block")
            return None

    def _usage_out(self) -> Usage:
        def number(*path: str) -> int:
            value = get(self._usage, *path)
            return (
                value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0
            )

        thinking = number("output_tokens_details", "thinking_tokens")
        # The provider counts cached prompt tokens apart from its input, and
        # its thinking inside its output; the thinking is taken out, so no
        # class holds another. Where it reports no split, thinking stays in
        # the output, which bills at the same rate.
        return Usage(
            input=number("input_tokens"),
            cache_read=number("cache_read_input_tokens"),
            cache_write=number("cache_creation_input_tokens"),
            output=max(0, number("output_tokens") - thinking),
            thinking=thinking,
        )

    def _build(self, *, whole: bool) -> ModelReply:
        stop = STOP_REASONS.get(self._stop or "") if whole else None
        blocks, thinking = self._blocks_out(finished=stop is StopReason.TOOL_USE)
        if whole and self._stop and stop is None:
            self._drop(
                f"stop reason {self._stop}", "unknown, so the reply is recorded as truncated"
            )
        return ModelReply(
            blocks=tuple(blocks),
            thinking=tuple(thinking),
            stop_reason=stop,
            truncated=stop is None or stop is StopReason.OUTPUT_LIMIT,
            usage=self._usage_out(),
            model=self._reported[:MAX_NAME],
            response_id=self._id[:MAX_NAME] if self._id else None,
            dropped=tuple(self._dropped),
        )

    def partial(self) -> ModelReply:
        """What arrived so far, truncated."""
        return self._build(whole=False)

    def reply(self) -> ModelReply:
        """The reply, once the stream finished; a stream that ended before its
        last event is broken, and fails as `transient` with what arrived."""
        if not self._finished:
            raise ModelCallFailed(
                ErrorKind.TRANSIENT,
                "the stream ended before the message did",
                partial=self.partial(),
            )
        return self._build(whole=True)


class ModelProviderAnthropicImpl(ModelProviderInterface):
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
        self._url = base_url.rstrip("/") + "/v1/messages"
        self._timeout = httpx.Timeout(timeout.total_seconds())

    @property
    def provider(self) -> ProviderName:
        return ProviderName.ANTHROPIC

    async def stream(
        self, call: ModelCall, *, credential: SecretStr | None = None
    ) -> AsyncIterator[StreamPart]:
        key = key_for("Anthropic", credential, self._api_key)
        body, dropped = request_body(call)
        folded = AnthropicReply(call.model, dropped)
        headers = {"x-api-key": key.get_secret_value(), "anthropic-version": API_VERSION}
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
                        authentication=unauthenticated(raw),
                    )
                async for event, data in sse_events(response.aiter_lines()):
                    for part in folded.feed(event, data):
                        yield part
        except httpx.HTTPError as error:
            raise ModelCallFailed(
                ErrorKind.TRANSIENT,
                f"Anthropic did not answer: {type(error).__name__}",
                partial=folded.partial(),
            ) from None
        yield Finished(reply=folded.reply())

    def describe(self) -> str:
        configured = "a platform key" if self._api_key is not None else "no platform key"
        return f"model provider anthropic: the Messages API, {configured}"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        await self._http.aclose()
