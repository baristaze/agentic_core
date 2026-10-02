"""An agent's tool registry: its power. An agent without a workspace tool
cannot touch a workspace, whatever the model asks for, because a call
resolves only to a tool the registry holds. Tools from native code, MCP
servers, and sub-agents take one contract here.

The registry renders in a fixed order, by name, so the prompt prefix stays
stable however the tools were listed. It refuses, when it is built, two
tools of one name, a class it does not know, and a mode its tool does not
implement: a misspelt class would escape every rule that names the right
one."""

from collections.abc import Iterable

from acme.om.tools.tool import JobToolInterface, ToolInterface
from acme.om.tools.types.call import JobStarted
from acme.om.tools.types.tool import ToolClass, ToolDefinition, ToolMode


class ToolRegistry:
    def __init__(self, tools: Iterable[ToolInterface], domain_classes: Iterable[str] = ()) -> None:
        known = {*(c.value for c in ToolClass), *domain_classes}
        ordered = sorted(tools, key=lambda tool: tool.spec.name)
        names = [tool.spec.name for tool in ordered]
        if len(set(names)) != len(names):
            raise ValueError("a registry holds one tool of each name")
        for tool in ordered:
            spec = tool.spec
            if spec.authorization_class not in known:
                raise ValueError(f"{spec.name}: no class {spec.authorization_class!r}")
            is_job = isinstance(tool, JobToolInterface)
            if (spec.mode is ToolMode.JOB) != is_job:
                raise ValueError(f"{spec.name}: a job tool, and only one, is in job mode")
            if is_job and spec.output_model is not JobStarted:
                raise ValueError(f"{spec.name}: a job tool answers JobStarted")
        self._tools = tuple(ordered)
        self._by_name = {tool.spec.name: tool for tool in ordered}

    def get(self, name: str) -> ToolInterface | None:
        """The tool of that name, or None: a name the model chose that the
        registry does not hold resolves to nothing."""
        return self._by_name.get(name)

    def tools(self) -> tuple[ToolInterface, ...]:
        return self._tools

    def classes(self) -> frozenset[str]:
        """Every class of call the registry offers: what a principal must be
        allowed to make to start or instruct a session of its kind."""
        return frozenset(tool.spec.authorization_class for tool in self._tools)

    def render(self) -> tuple[ToolDefinition, ...]:
        return tuple(
            ToolDefinition(
                name=tool.spec.name,
                description=tool.spec.description,
                input_schema=tool.spec.input_model.model_json_schema(),
            )
            for tool in self._tools
        )
