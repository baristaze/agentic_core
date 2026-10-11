"""The MCP client over the official Python SDK, pinned. One connection an
exchange: a stdio server is started for it, with its error stream dropped,
since a server may print what it was given there; an HTTP server is
reached by a client of the exchange's own that carries the credential's
header, follows no redirect off the server's origin, and closes with it.

The exchange runs in one cancel scope that ends by the deadline. The SDK
stops a stdio server inside a shield when its connection closes, so a
server that hangs is stopped before the timeout reaches the caller. What
the SDK raises is read here into the client's own errors, and dropped:
its text quotes what the server sent."""

import json
import os
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TextIO, cast
from uuid import UUID

import anyio
import httpx2
from mcp.client import Client, IncomingMessage
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp_types import (
    CONNECTION_CLOSED,
    INVALID_REQUEST,
    PARSE_ERROR,
    AudioContent,
    BlobResourceContents,
    CallToolResult,
    EmbeddedResource,
    ImageContent,
    ResourceLink,
    TextContent,
    TextResourceContents,
    Tool,
)
from pydantic import SecretStr, ValidationError

from acme.infra.base import utcnow
from acme.infra.exceptions import InfraException
from acme.infra.transports.redaction import Redactor
from acme.integrations.exceptions import (
    McpServerFailed,
    McpServerRefused,
    McpServerUnavailable,
    McpToolRefused,
)
from acme.integrations.mcp import (
    McpAnswer,
    McpClientInterface,
    McpHttp,
    McpListedTool,
    McpServer,
    McpStdio,
    Refuse,
)

KEY_META = "idempotencyKey"
"""Where a call's idempotency key travels: its request's `_meta`."""

REASON_CHARS = 500
"""The most of a server's own reason for a refusal the client passes on."""

# The connect, write, and pool timeouts of an HTTP exchange. A read has no
# bound of its own: the deadline, the tool's own time, bounds it with the
# rest. A read cut short sooner would report a call the server ran as one
# that never reached it, and the model would make it again.
HTTP_TIMEOUT = httpx2.Timeout(30.0, read=None)

AUTH_STATUSES = frozenset({401, 403})
TOO_MANY = 429


@dataclass(frozen=True)
class _Refusal:
    reason: str


@dataclass
class _Answered:
    """The status of the last answer an HTTP server gave a request: a POST,
    since the stream a GET opens answers on its own."""

    status: int | None = None

    async def note(self, response: httpx2.Response) -> None:
        if response.request.method == "POST":
            self.status = response.status_code if response.status_code >= 400 else None


class McpClientSdkImpl(McpClientInterface):
    async def list_tools(
        self, server: McpServer, credential: SecretStr | None, *, deadline: datetime
    ) -> tuple[McpListedTool, ...]:
        redactor = _redactor(server, credential)

        async def work(client: Client) -> tuple[McpListedTool, ...]:
            return await _listing(client, server, redactor)

        return await _exchange(server, credential, deadline, redactor, work)

    async def call_tool(
        self,
        server: McpServer,
        credential: SecretStr | None,
        tool: str,
        arguments: Mapping[str, Any],
        *,
        key: UUID,
        refuse: Refuse,
        deadline: datetime,
    ) -> McpAnswer:
        redactor = _redactor(server, credential)

        async def work(client: Client) -> McpAnswer | _Refusal:
            listed = await _listing(client, server, redactor)
            reason = refuse(next((each for each in listed if each.name == tool), None))
            if reason is not None:
                return _Refusal(reason)
            result = await client.call_tool(tool, dict(arguments), meta={KEY_META: str(key)})
            return _answer(result, redactor)

        outcome = await _exchange(server, credential, deadline, redactor, work)
        if isinstance(outcome, _Refusal):
            raise McpToolRefused(outcome.reason)
        return outcome


async def _exchange[T](
    server: McpServer,
    credential: SecretStr | None,
    deadline: datetime,
    redactor: Redactor,
    work: Callable[[Client], Awaitable[T]],
) -> T:
    """One connection to the server, its work, and its close, by the
    deadline. A line the transport cannot read as a message ends the
    exchange at once: the SDK would wait on the answer it never parsed.
    Nothing the SDK raises leaves: it is read into the client's own error,
    with no cause attached."""
    seconds = (deadline - utcnow()).total_seconds()
    if seconds <= 0:
        raise TimeoutError(f"{server.name}: no time is left for the exchange")
    failure: InfraException = McpServerFailed(f"{server.name} sent something that is not MCP")
    answered = _Answered()
    try:
        with anyio.fail_after(seconds), anyio.CancelScope() as unreadable:

            async def on_message(message: IncomingMessage) -> None:
                if isinstance(message, Exception):
                    unreadable.cancel()

            async with AsyncExitStack() as stack:
                transport = await _transport(stack, server, credential, answered)
                client = await stack.enter_async_context(
                    Client(
                        transport,
                        cache=None,
                        message_handler=on_message,
                        read_timeout_seconds=seconds,
                    )
                )
                return await work(client)
    except TimeoutError:
        raise TimeoutError(f"{server.name} did not answer in time") from None
    except Exception as error:  # an exception group too, which is one
        failure = _failure(server, error, redactor, answered.status)
    raise failure


async def _transport(
    stack: AsyncExitStack, server: McpServer, credential: SecretStr | None, answered: _Answered
) -> Any:
    """The SDK's transport to the server, its resources on `stack`. Over
    HTTP, `answered` keeps the status of each answer to a request."""
    value = credential.get_secret_value() if credential is not None else None
    match server.transport:
        case McpStdio() as stdio:
            env = dict(stdio.env)
            if stdio.credential_env is not None and value is not None:
                env[stdio.credential_env] = value
            errors = await anyio.open_file(os.devnull, "w")
            stack.push_async_callback(errors.aclose)
            parameters = StdioServerParameters(
                command=stdio.command, args=list(stdio.args), env=env, cwd=stdio.cwd
            )
            return stdio_client(parameters, errlog=cast("TextIO", errors.wrapped))
        case McpHttp() as http:
            headers: dict[str, str] = {}
            if value is not None:
                scheme = http.credential_scheme
                headers[http.credential_header] = f"{scheme} {value}" if scheme else value
            client = await stack.enter_async_context(
                httpx2.AsyncClient(
                    headers=headers, timeout=HTTP_TIMEOUT, event_hooks={"response": [answered.note]}
                )
            )
            return streamable_http_client(http.url, http_client=client)


async def _listing(
    client: Client, server: McpServer, redactor: Redactor
) -> tuple[McpListedTool, ...]:
    tools: list[Tool] = []
    cursor: str | None = None
    while True:
        page = await client.list_tools(cursor=cursor)
        tools.extend(page.tools)
        cursor = page.next_cursor
        if cursor is None:
            break
    listed = tuple(_listed(tool, redactor) for tool in tools)
    names = [tool.name for tool in listed]
    if len(set(names)) != len(names):
        raise McpServerFailed(f"{server.name} lists a tool twice")
    return listed


def _listed(tool: Tool, redactor: Redactor) -> McpListedTool:
    annotations = (
        tool.annotations.model_dump(mode="json", by_alias=True, exclude_none=True)
        if tool.annotations is not None
        else {}
    )
    return McpListedTool(
        name=redactor.redact(tool.name),
        description=redactor.redact(tool.description or ""),
        input_schema=_redacted(tool.input_schema, redactor),
        annotations=_redacted(annotations, redactor),
    )


def _redacted(value: Any, redactor: Redactor) -> Any:
    """`value` with every string in it, a key included, redacted."""
    if isinstance(value, str):
        return redactor.redact(value)
    if isinstance(value, Mapping):
        return {_redacted(k, redactor): _redacted(v, redactor) for k, v in value.items()}  # type: ignore[misc]
    if isinstance(value, list | tuple):
        return [_redacted(item, redactor) for item in value]  # type: ignore[misc]
    return value


def _answer(result: CallToolResult, redactor: Redactor) -> McpAnswer:
    parts: list[str] = []
    for block in result.content:
        match block:
            case TextContent():
                parts.append(block.text)
            case ImageContent() | AudioContent():
                parts.append(f"[{block.type} {block.mime_type}, not passed on]")
            case ResourceLink():
                parts.append(f"[a link to {block.uri}]")
            case EmbeddedResource(resource=BlobResourceContents() as blob):
                parts.append(f"[{blob.mime_type or 'a blob'} at {blob.uri}, not passed on]")
            case EmbeddedResource(resource=TextResourceContents() as resource):
                parts.append(resource.text)
    if not parts and result.structured_content is not None:
        parts.append(json.dumps(result.structured_content, sort_keys=True))
    return McpAnswer(text=redactor.redact("\n".join(parts)), is_error=result.is_error)


def _redactor(server: McpServer, credential: SecretStr | None) -> Redactor:
    if credential is None or server.secret is None:
        return Redactor({})
    return Redactor({server.secret: credential.get_secret_value()})


def _failure(
    server: McpServer, error: BaseException, redactor: Redactor, status: int | None
) -> InfraException:
    """The client's own error for what the SDK, or the transport under it,
    raised: written here, never the SDK's text, which may quote the
    server. Over HTTP, the status of the last answer to a request decides
    first, since the SDK turns it into a protocol error that drops it: a
    refused credential, a 429, and a server error are unavailable, and the
    credential is named, never its value; any other refusal is the
    request's."""
    leaf = _leaf(error)
    name = server.name
    reason = redactor.redact(leaf.message)[:REASON_CHARS] if isinstance(leaf, MCPError) else ""
    if status is not None and status in AUTH_STATUSES:
        what = (
            f"the credential {server.secret!r}" if server.secret else "a request with no credential"
        )
        return McpServerUnavailable(f"{name} answered {status}: it did not accept {what}")
    if status is not None and (status == TOO_MANY or status >= 500):
        return McpServerUnavailable(f"{name} answered {status}")
    if status is not None and status >= 400:
        return McpServerRefused(f"{name} answered {status}: {reason}")
    match leaf:
        case McpServerFailed() | McpServerRefused() | McpServerUnavailable():
            return leaf
        case MCPError() if leaf.code in {PARSE_ERROR, INVALID_REQUEST}:
            return McpServerFailed(f"{name} answered something that is not MCP")
        case MCPError() if leaf.code == CONNECTION_CLOSED:
            return McpServerFailed(f"{name} closed the connection")
        case MCPError():
            return McpServerRefused(f"{name} refused the request ({leaf.code}): {reason}")
        case ValidationError() | json.JSONDecodeError():
            return McpServerFailed(f"{name} answered something that is not MCP")
        case httpx2.TransportError() | OSError():
            return McpServerUnavailable(f"{name} could not be reached")
        case _:
            return McpServerFailed(f"{name} failed: {type(leaf).__name__}")


def _leaf(error: BaseException) -> BaseException:
    """The one error an exception group holds, or the error itself."""
    while isinstance(error, BaseExceptionGroup) and len(error.exceptions) == 1:
        error = error.exceptions[0]
    return error
