"""The MCP client's own rules, short of a real server: where a server's
credential may go, and what an HTTP server's refusal is. A refused
credential, a 429, and a server error are unavailable, so work waits for
them; any other refusal is the request's (ADR 0051). The credential is
named, never its value. The tools over a real server are the object
model's tests (`test_tools_mcp_servers.py`). Each logger the SDK writes
to writes nothing, since its lines quote what a server sent."""

import re
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import mcp
import mcp_types
import pytest
from pydantic import SecretStr, ValidationError

from acme.infra.base import utcnow
from acme.infra.observability import SILENT_LOGGERS
from acme.integrations.mcp import (
    McpHttp,
    McpServer,
    McpServerRefused,
    McpServerUnavailable,
    McpStdio,
)
from acme.integrations.mcp.sdk import McpClientSdkImpl

CREDENTIAL = "tok-5b1e09c4d2"


def test_a_credential_goes_over_https_or_to_this_host_and_never_in_an_address() -> None:
    for url in (
        "https://tools.example.com/mcp",
        "http://127.0.0.1:8341/mcp",
        "http://localhost/mcp",
    ):
        McpServer(name="t", transport=McpHttp(url=url), secret="t_token")
    with pytest.raises(ValidationError, match="over https"):
        McpServer(name="t", transport=McpHttp(url="http://tools.example.com/mcp"), secret="t")
    McpServer(name="t", transport=McpHttp(url="http://tools.example.com/mcp"))
    with pytest.raises(ValidationError, match="carries no credential"):
        McpHttp(url="https://user:pass@tools.example.com/mcp")


def test_a_stdio_server_names_the_variable_its_credential_lands_in() -> None:
    McpServer(name="t", transport=McpStdio(command="t", credential_env="T_TOKEN"), secret="t")
    with pytest.raises(ValidationError, match="names the variable"):
        McpServer(name="t", transport=McpStdio(command="t"), secret="t")
    with pytest.raises(ValidationError, match="names the variable"):
        McpServer(name="t", transport=McpStdio(command="t", credential_env="T_TOKEN"))
    with pytest.raises(ValidationError, match="each variable once"):
        McpStdio(command="t", env=(("T_TOKEN", "x"),), credential_env="T_TOKEN")


@dataclass
class Answering:
    """A server that answers every request with `status`, and no body."""

    port: int
    status: int = 200


@pytest.fixture
def answering() -> Iterator[Answering]:
    held = Answering(port=0)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("content-length", "0")))
            self.send_response(held.status)
            self.send_header("content-length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    held.port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield held
    finally:
        server.shutdown()
        thread.join()


@pytest.mark.parametrize(
    ("status", "raised", "says"),
    [
        (401, McpServerUnavailable, "did not accept the credential 't_token'"),
        (403, McpServerUnavailable, "did not accept the credential 't_token'"),
        (429, McpServerUnavailable, "answered 429"),
        (503, McpServerUnavailable, "answered 503"),
        (400, McpServerRefused, "answered 400"),
    ],
)
async def test_an_http_refusal_is_read_by_whose_problem_it_is(
    answering: Answering, status: int, raised: type[Exception], says: str
) -> None:
    answering.status = status
    url = f"http://127.0.0.1:{answering.port}/mcp"
    server = McpServer(name="t", transport=McpHttp(url=url), secret="t_token")
    with pytest.raises(raised) as refusal:
        await McpClientSdkImpl().list_tools(
            server, SecretStr(CREDENTIAL), deadline=utcnow() + timedelta(seconds=10)
        )
    assert says in str(refusal.value)
    assert CREDENTIAL not in str(refusal.value)
    assert refusal.value.__cause__ is None and refusal.value.__context__ is None


async def test_a_server_that_does_not_start_is_unavailable_and_no_time_starts_none() -> None:
    server = McpServer(name="t", transport=McpStdio(command="/nonexistent/server"))
    with pytest.raises(McpServerUnavailable, match="could not be reached"):
        await McpClientSdkImpl().list_tools(server, None, deadline=utcnow() + timedelta(seconds=10))
    with pytest.raises(TimeoutError):
        await McpClientSdkImpl().list_tools(server, None, deadline=utcnow() - timedelta(seconds=1))


LOGGER_NAMED = re.compile(r"get_?[lL]ogger\(\s*[\"']([^\"']+)[\"']")


def test_each_logger_the_sdk_writes_to_writes_nothing() -> None:
    """The SDK's modules the client loads write to `mcp` and its children,
    and to any logger they name themselves. Read from the installed
    package, so a version that names another fails here. Its command-line
    entry points name others, and the engine never loads them."""
    named: dict[str, str] = {}
    for package in (mcp, mcp_types):
        assert package.__file__ is not None
        root = Path(package.__file__).parent
        for source in root.rglob("*.py"):
            parts = source.relative_to(root.parent).with_suffix("").parts
            module = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
            if module in sys.modules:
                for name in LOGGER_NAMED.findall(source.read_text()):
                    named[name] = module
    assert named.get("client") == "mcp.client.session"
    for name, module in named.items():
        silent = any(name == each or name.startswith(f"{each}.") for each in SILENT_LOGGERS)
        assert silent, f"{module} writes to the logger {name!r}"
