"""A tool over MCP, under the one contract: its spec is the adopter's
binding, its description the server's, as pinned. The call itself goes
through `McpCall`, the adopter's client of the server."""

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from acme.om.base import Platform
from acme.om.context import TenantContext
from acme.om.exceptions import McpDefinitionChanged
from acme.om.tools.rules import definition_hash
from acme.om.tools.tool import ToolInterface, ToolRuntime
from acme.om.tools.types.mcp import McpBinding, McpOutput, McpToolDefinition
from acme.om.tools.types.policy import Target
from acme.om.tools.types.tool import ToolInput, ToolMode, ToolSpec

McpCall = Callable[[TenantContext, str, str, Mapping[str, Any]], Awaitable[str]]
"""Calls a server's tool: the server, the tool's name there, and its input;
answers the result as text, or raises `ToolFailed` with its class."""


class McpToolImpl(ToolInterface):
    """Built from what the server lists now and the binding: refused when
    the two disagree on the server's tool or its definition's hash. The
    server's annotations play no part in the spec."""

    def __init__(self, definition: McpToolDefinition, binding: McpBinding, call: McpCall) -> None:
        if definition.name != binding.server_tool:
            raise McpDefinitionChanged(
                f"{binding.name} binds {binding.server_tool!r}, not {definition.name!r}"
            )
        found = definition_hash(definition)
        if found != binding.definition_hash:
            raise McpDefinitionChanged(
                f"{binding.server}/{definition.name} hashes to {found}, "
                f"not the reviewed {binding.definition_hash}"
            )
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
            self._binding.server,
            self._binding.server_tool,
            call_input.model_dump(mode="json"),
        )
        return McpOutput(text=text)
