"""The Model Context Protocol from the client's side: a server reached over
stdio or streamable HTTP, the tools it lists, and one call of one of them.
What a server's tool may do is the object model's to decide; this package
speaks the protocol and nothing more (ADR 1034).

Each exchange is one connection. The client connects, lists the server's
tools, and closes, or lists them, lets its caller admit the tool it is
about to call as the server lists it now, calls it, and closes. A server
over stdio is a program the adopter names, started for the connection and
stopped with it; its environment holds the variables its configuration
names and its credential, and nothing of the engine's. A server over HTTP
takes its credential in a header, over https or to this host alone.

The credential is the caller's, resolved for the one connection. It
reaches the server and nothing else: every text the server sends back,
its listing, its answer, and its error, is redacted of it before it leaves
the client, and the client's errors are written in its own words. A call
ends by its deadline, the connection with it."""

import ipaddress
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, SecretStr, model_validator

from acme.infra.base import FrozenMapping, InfraModel
from acme.integrations.exceptions import (
    McpServerFailed,
    McpServerRefused,
    McpServerUnavailable,
    McpToolRefused,
)

__all__ = [
    "McpAnswer",
    "McpClientInterface",
    "McpHttp",
    "McpListedTool",
    "McpServer",
    "McpServerFailed",
    "McpServerRefused",
    "McpServerUnavailable",
    "McpStdio",
    "McpToolRefused",
    "Refuse",
]

ENV_NAME = r"^[A-Z_][A-Z0-9_]*$"
HEADER_NAME = r"^[A-Za-z0-9-]{1,64}$"
SECRET_NAME = r"^[^/]+$"
LOOPBACK_HOSTS = frozenset({"localhost"})


class McpStdio(InfraModel):
    """A server the client starts as a program: its command, its arguments,
    its working directory, and the variables its environment holds beside
    the credential's, which `credential_env` names. `env` never holds a
    secret."""

    kind: Literal["stdio"] = "stdio"
    command: str = Field(min_length=1)
    args: tuple[str, ...] = ()
    env: tuple[tuple[str, str], ...] = ()
    cwd: str | None = None
    credential_env: str | None = Field(default=None, pattern=ENV_NAME)

    @model_validator(mode="after")
    def _one_name_one_variable(self) -> Self:
        names = [name for name, _ in self.env]
        if self.credential_env is not None:
            names.append(self.credential_env)
        if len(set(names)) != len(names):
            raise ValueError("a server's environment names each variable once")
        return self


class McpHttp(InfraModel):
    """A server at an address, over streamable HTTP. Its credential goes in
    `credential_header`, after `credential_scheme` and a space when the
    scheme is not empty."""

    kind: Literal["http"] = "http"
    url: str = Field(min_length=1)
    credential_header: str = Field(default="Authorization", pattern=HEADER_NAME)
    credential_scheme: str = "Bearer"

    @model_validator(mode="after")
    def _an_address(self) -> Self:
        parts = urlsplit(self.url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("a server's address is an http or https URL")
        if parts.username or parts.password:
            raise ValueError("a server's address carries no credential")
        return self

    @property
    def private(self) -> bool:
        """Whether what is sent there stays private on the way: https, or
        this host."""
        parts = urlsplit(self.url)
        if parts.scheme == "https":
            return True
        host = parts.hostname or ""
        if host in LOOPBACK_HOSTS:
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False


class McpServer(InfraModel):
    """A server the adopter binds tools of, by name. `secret` names the
    tenant's secret its calls carry, never its value; a server that takes
    none names none."""

    name: str = Field(min_length=1, max_length=64)
    transport: McpStdio | McpHttp = Field(discriminator="kind")
    secret: str | None = Field(default=None, min_length=1, max_length=200, pattern=SECRET_NAME)

    @model_validator(mode="after")
    def _a_credential_has_a_place(self) -> Self:
        match self.transport:
            case McpStdio(credential_env=env) if (env is None) != (self.secret is None):
                raise ValueError("a stdio server names the variable its credential lands in")
            case McpHttp() as http if self.secret is not None and not http.private:
                raise ValueError("a credential goes over https, or to this host")
            case _:
                return self


class McpListedTool(InfraModel):
    """A tool as the server lists it now, redacted of the credential: its
    name, its description, its input schema, and its annotations as the
    server wrote them."""

    name: str
    description: str = ""
    input_schema: FrozenMapping = Field(default_factory=dict, validate_default=True)
    annotations: FrozenMapping = Field(default_factory=dict, validate_default=True)


class McpAnswer(InfraModel):
    """What a call answered, as text: each part of the server's content in
    its order, one to a line, a part that is not text named and not passed
    on, and its structured content when it sent no other. `is_error` is the
    server's own word that the tool failed."""

    text: str
    is_error: bool = False


Refuse = Callable[[McpListedTool | None], str | None]
"""The caller's word on the tool it is about to call, as the server lists
it now (None when it lists none of that name): the reason it refuses it,
or None to call it."""


class McpClientInterface(ABC):
    """Every call takes the server, its credential for this connection (None
    when it names no secret), and the deadline the whole exchange ends by:
    `TimeoutError` past it, with the connection closed and a stdio server
    stopped. A failure is `McpServerUnavailable`, `McpServerFailed`, or
    `McpServerRefused`; none carries the credential."""

    @abstractmethod
    async def list_tools(
        self, server: McpServer, credential: SecretStr | None, *, deadline: datetime
    ) -> tuple[McpListedTool, ...]:
        """Every tool the server lists, every page of it. A listing that
        names a tool twice is `McpServerFailed`."""
        ...

    @abstractmethod
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
        """Lists the server's tools, asks `refuse` about `tool` as listed,
        and calls it, all on one connection. A refusal is `McpToolRefused`
        with the caller's reason, and the call is never sent. The call
        carries `key` in its request's metadata, as `idempotencyKey`, so a
        server that keeps one does once what a repeat asks again."""
        ...
