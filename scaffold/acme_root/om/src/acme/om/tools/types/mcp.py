"""A tool served over the Model Context Protocol takes the same contract as
any other. The adopter's registry binds each one: its class, its effect, its
timeout, its typed input, and the hash of the definition it reviewed. What
the server says of a tool, its annotations included, is a hint and never
authority; a definition that no longer hashes to its pin is not served until
someone reviews it and moves the pin."""

from datetime import timedelta

from pydantic import Field

from acme.om.base import FrozenMapping, Platform
from acme.om.tools.types.policy import Target
from acme.om.tools.types.tool import CLASS_NAME, TOOL_NAME, Effect, ToolInput


class McpToolDefinition(Platform):
    """A tool as its server lists it."""

    name: str = Field(min_length=1)
    description: str = ""
    input_schema: FrozenMapping = Field(default_factory=dict, validate_default=True)
    annotations: FrozenMapping = Field(default_factory=dict, validate_default=True)


class McpBinding(Platform):
    """What the adopter assigns one server's tool. `definition_hash` pins the
    definition it reviewed (`tools.mcp.definition_hash`); `target` is what
    every call of the tool acts on, as the adopter knows it."""

    server: str = Field(min_length=1)
    server_tool: str = Field(min_length=1)
    name: str = Field(pattern=TOOL_NAME)
    input_model: type[ToolInput]
    timeout: timedelta = Field(gt=timedelta(0))
    authorization_class: str = Field(pattern=CLASS_NAME)
    effect: Effect
    interruptible: bool = False
    target: Target = Field(default_factory=Target)
    definition_hash: str = Field(min_length=1)


class McpOutput(Platform):
    text: str
