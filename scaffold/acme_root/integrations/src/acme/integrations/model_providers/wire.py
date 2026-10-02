"""What the adapters share on the wire: the server-sent events a streamed
response arrives as, the provider's own wait before a retry, and a body
read as JSON without trusting it to be any."""

import json
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from pydantic import SecretStr

from acme.integrations.model_providers.failures import ModelCallFailed
from acme.integrations.model_providers.types import ErrorKind

MAX_MESSAGE = 500
"""The longest provider message a failure carries, for the log."""


async def sse_events(lines: AsyncIterator[str]) -> AsyncIterator[tuple[str, str]]:
    """Each event of a server-sent stream as its name and its data. A comment
    line is skipped, and the data of an event spread over several lines is
    joined, as the format says."""
    event, data = "", []
    async for raw in lines:
        line = raw.rstrip("\r")
        if not line:
            if data:
                yield event or "message", "\n".join(data)
            event, data = "", []
        elif line.startswith(":"):
            continue
        else:
            name, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if name == "event":
                event = value
            elif name == "data":
                data.append(value)
    if data:
        yield event or "message", "\n".join(data)


def json_object(text: str | bytes) -> dict[str, Any]:
    """A body as a JSON object, or empty when it is not one: a proxy's HTML
    page, a cut-off stream, nothing at all."""
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def field(value: Any, *path: str) -> Any:
    """A nested field of a decoded body, or None when any step of the path is
    missing or is not an object."""
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def retry_after(headers: httpx.Headers, now: datetime | None = None) -> float | None:
    """The provider's own wait before a retry, in seconds: `retry-after-ms`
    when it sends one, else `retry-after` as seconds or as a date."""
    millis = headers.get("retry-after-ms")
    if millis is not None:
        try:
            return max(0.0, float(millis) / 1000)
        except ValueError:
            pass
    value = headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except TypeError, ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())


def key_for(provider: str, credential: SecretStr | None, platform: SecretStr | None) -> SecretStr:
    """The key a call runs on: its own credential when it carries one, the
    platform's only when it carries none. An empty credential is refused,
    never taken for a missing one, so a call meant for a tenant's key never
    runs on the platform's."""
    if credential is not None:
        if not credential.get_secret_value().strip():
            raise ModelCallFailed(ErrorKind.CREDENTIAL, f"the call's own {provider} key is empty")
        return credential
    if platform is None or not platform.get_secret_value().strip():
        raise ModelCallFailed(ErrorKind.CREDENTIAL, f"no {provider} key for this call")
    return platform


def clipped(message: str) -> str:
    return message if len(message) <= MAX_MESSAGE else message[:MAX_MESSAGE] + "..."
