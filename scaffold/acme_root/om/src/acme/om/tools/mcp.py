"""A tool over MCP, under the one contract: its spec is the adopter's
binding, its description the server's, as pinned. The call itself goes
through `McpCall`; `McpServers` is the engine's, over the MCP client
(ADR 1034).

`McpServers` lists the servers an adopter binds tools of and builds a tool
of each binding whose definition is the one its pin names; it refuses the
lot, with each reason, when a definition moved. Each call it makes lists
the server's tools again on the call's own connection and refuses the tool
when its definition moved since, so a server's changed tool never runs
and never answers a model before someone reviews it and moves its pin.

A server's credential is a tenant's secret, named by the server. It is
resolved for one call, at the call, under the call's tenant, and handed to
the client, which redacts it from everything the server sends back. What
the server answers is data: its text becomes the tool's output, which the
response renders as any tool's output, quoted in the result block."""

from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import SecretStr

from acme.infra.base import utcnow
from acme.infra.secrets import SecretsInterface
from acme.integrations.mcp import McpClientInterface, McpListedTool, McpServer, McpToolRefused
from acme.om.base import Platform
from acme.om.context import TenantContext
from acme.om.exceptions import McpDefinitionChanged, ToolFailed, ValidationFailed
from acme.om.steps.types.header import ToolFailure
from acme.om.tools.rules import unreviewed
from acme.om.tools.tool import ToolInterface, ToolRuntime
from acme.om.tools.types.mcp import McpBinding, McpOutput, McpToolDefinition
from acme.om.tools.types.policy import Target
from acme.om.tools.types.tool import ToolInput, ToolMode, ToolSpec
from acme.om.tools.types.toolset import Toolset

McpCall = Callable[[TenantContext, McpBinding, Mapping[str, Any], UUID, datetime], Awaitable[str]]
"""Calls a server's tool as its binding names it: its input, the call's
idempotency key, which the client passes to the server so a repeat after a
crash does once what the first did, and the deadline the call ends by.
Answers the result as text, or raises `ToolFailed` with its class, or
`McpDefinitionChanged` when the tool no longer matches its pin."""

LIST_TIMEOUT = timedelta(seconds=30)
"""How long a listing of the servers a set of bindings names may take, when
its caller sets no deadline."""


class McpToolImpl(ToolInterface):
    """Built from what the server lists now and the binding: refused when
    the two disagree on the server's tool or its definition's hash. The
    server's annotations play no part in the spec."""

    def __init__(self, definition: McpToolDefinition, binding: McpBinding, call: McpCall) -> None:
        reason = unreviewed(binding, definition)
        if reason is not None:
            raise McpDefinitionChanged(reason)
        self._binding = binding
        self._call = call
        self._spec = ToolSpec(
            name=binding.name,
            description=definition.description or binding.name,
            input_model=binding.input_model,
            output_model=McpOutput,
            timeout=binding.timeout,
            authorization_class=binding.authorization_class,
            effect=binding.effect,
            interruptible=binding.interruptible,
            mode=ToolMode.SYNC,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def target(self, ctx: TenantContext, call_input: ToolInput) -> Target:
        return self._binding.target

    async def preflight(
        self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime
    ) -> None:
        return None

    async def run(
        self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime
    ) -> Platform:
        text = await self._call(
            ctx,
            self._binding,
            call_input.model_dump(mode="json"),
            runtime.key,
            runtime.deadline,
        )
        return McpOutput(text=text)


def toolset_of(bindings: Sequence[McpBinding], *, name: str, version: int, summary: str) -> Toolset:
    """A toolset of a server's bound tools, by their names in the registry:
    what a kind names, or defers, to take them."""
    return Toolset(
        name=name, version=version, summary=summary, tools=tuple(b.name for b in bindings)
    )


class McpServers:
    """The servers an adopter binds tools of, by name, reached through the
    MCP client. `tools` builds the tools of a set of bindings; `call` is
    the `McpCall` each of them goes through."""

    def __init__(
        self,
        client: McpClientInterface,
        secrets: SecretsInterface,
        servers: Iterable[McpServer],
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        held = tuple(servers)
        self._servers = {server.name: server for server in held}
        if len(self._servers) != len(held):
            raise ValueError("each server is named once")
        self._client = client
        self._secrets = secrets
        self._clock = clock

    async def tools(
        self,
        bindings: Sequence[McpBinding],
        *,
        org_id: UUID | None = None,
        deadline: datetime | None = None,
    ) -> tuple[McpToolImpl, ...]:
        """A tool of each binding, built from its server's listing now: each
        server the bindings name is listed once, under `org_id`'s
        credential when it names a secret, by `deadline`, or within
        `LIST_TIMEOUT` when none is given. `McpDefinitionChanged`, naming
        every binding the listing does not match and why, when any moved:
        none is served until someone reviews it and moves its pin."""
        deadline = deadline or self._clock() + LIST_TIMEOUT
        built: list[McpToolImpl] = []
        refused: list[str] = []
        for name in dict.fromkeys(binding.server for binding in bindings):
            server = self._server(name)
            credential = await self._credential(server, org_id, deadline)
            listed = await self._client.list_tools(server, credential, deadline=deadline)
            found = {tool.name: definition_of(tool) for tool in listed}
            for binding in (each for each in bindings if each.server == name):
                definition = found.get(binding.server_tool)
                reason = unreviewed(binding, definition)
                if reason is not None or definition is None:
                    refused.append(reason or binding.name)
                    continue
                built.append(McpToolImpl(definition, binding, self.call))
        if refused:
            raise McpDefinitionChanged("; ".join(refused))
        return tuple(built)

    async def call(
        self,
        ctx: TenantContext,
        binding: McpBinding,
        call_input: Mapping[str, Any],
        key: UUID,
        deadline: datetime,
    ) -> str:
        """One call, on a connection of its own: the server's credential
        resolved for it under the call's tenant, the tool admitted only as
        its pin names it, and its answer as text. The server's own word
        that the tool failed is a `permanent` failure the model reads."""
        server = self._server(binding.server)
        credential = await self._credential(server, ctx.org_id, deadline)

        def refuse(listed: McpListedTool | None) -> str | None:
            return unreviewed(binding, None if listed is None else definition_of(listed))

        try:
            answer = await self._client.call_tool(
                server,
                credential,
                binding.server_tool,
                call_input,
                key=key,
                refuse=refuse,
                deadline=deadline,
            )
        except McpToolRefused as refusal:
            raise McpDefinitionChanged(refusal.message) from None
        except TimeoutError:
            raise ToolFailed(
                ToolFailure.TIMEOUT, f"{binding.name}: {server.name} did not answer in time"
            ) from None
        if answer.is_error:
            raise ToolFailed(
                ToolFailure.PERMANENT, f"{binding.name} failed on {server.name}: {answer.text}"
            )
        return answer.text

    def _server(self, name: str) -> McpServer:
        server = self._servers.get(name)
        if server is None:
            raise ValidationFailed(f"no MCP server {name!r} is configured")
        return server

    async def _credential(
        self, server: McpServer, org_id: UUID | None, deadline: datetime
    ) -> SecretStr | None:
        """The server's credential, resolved now for one exchange: None for a
        server that names no secret. A server that names one is reached
        under a tenant, whose secret it is."""
        if server.secret is None:
            return None
        if org_id is None:
            raise ValidationFailed(f"{server.name} takes a tenant's credential: name the tenant")
        return SecretStr(await self._secrets.get(org_id, server.secret, deadline=deadline))


def definition_of(listed: McpListedTool) -> McpToolDefinition:
    """A listed tool as the binding's pin reads it."""
    return McpToolDefinition.model_validate(listed.model_dump(mode="json"))
