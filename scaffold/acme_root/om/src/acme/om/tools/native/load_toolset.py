"""`load_toolset`: the agent loads a toolset its kind defers, and the
toolset's tools join its registry from its next request on, for the rest
of the session. Its description names each toolset the kind defers in one
line, its name and its summary, and stays the same for the session's
life, so the tool definitions before a load keep their bytes.

A load changes the session's registry, which only its loop holds, so the
loop answers the call, as it answers a result tool: it writes the answer
and a `switched` step that names the toolset and the tools it added, in
one append (ADR 1031). The registry is a projection of the history: the
tools the session may call that wait for no load, and those its loads
added (`callable_tools`). The tools a load adds render after every tool
the registry held before it, so the next request's tool definitions are
the last one's with the new tools after them, and a replay of the history
renders the same requests."""

from collections.abc import Collection, Mapping, Sequence
from datetime import timedelta
from typing import Any

from pydantic import Field, ValidationError

from acme.om.base import Platform
from acme.om.context import TenantContext
from acme.om.exceptions import ToolFailed
from acme.om.steps.types.header import SwitchedHeader, ToolFailure, ToolsetSwitch
from acme.om.steps.types.step import Step
from acme.om.tools.tool import ToolInterface, ToolRuntime
from acme.om.tools.types.policy import Target
from acme.om.tools.types.tool import Effect, ToolClass, ToolInput, ToolMode, ToolSpec
from acme.om.tools.types.toolset import LOAD_TOOLSET, TOOLSET_NAME, Toolset

DESCRIPTION = (
    "Load a toolset: its tools are yours to call from your next turn on, "
    "for the rest of the session. Load one only when the work needs its "
    "tools. The toolsets you may load:"
)


class LoadToolsetInput(ToolInput):
    toolset: str = Field(pattern=TOOLSET_NAME)


class ToolsetLoaded(Platform):
    """What a load answers: the toolset, its version, and the tools it
    added, none when every one was already in the registry."""

    toolset: str
    version: int
    tools: tuple[str, ...]


class Load(Platform):
    """A load as the loop records it: the answer the model reads, and the
    switch it writes after it, None when the load adds nothing."""

    text: str
    switch: ToolsetSwitch | None = None


def description(deferred: Sequence[Toolset]) -> str:
    """`load_toolset`'s description for a kind that defers `deferred`: one
    line for each, in the order the kind names them."""
    lines = (f"- {toolset.name}: {toolset.summary}" for toolset in deferred)
    return "\n".join((DESCRIPTION, *lines))


def loads(steps: Sequence[Step]) -> tuple[ToolsetSwitch, ...]:
    """Every load the history records, in the order they happened."""
    return tuple(
        step.header.toolset
        for step in steps
        if isinstance(step.header, SwitchedHeader) and step.header.toolset is not None
    )


def callable_tools(
    holds: Collection[str], waiting: Collection[str], loaded: Sequence[ToolsetSwitch]
) -> frozenset[str]:
    """What a session's registry holds: each tool it may call (`holds`) that
    waits for no load, each one a load added, and `load_toolset` while any
    tool waits. The load adds no power of its own, only tools the session
    may call, so a sub-agent whose tools were cut to a parent's that lacks
    it still loads what it holds."""
    added = {tool for load in loaded for tool in load.tools}
    names = {tool for tool in holds if tool not in waiting or tool in added}
    return frozenset({*names, LOAD_TOOLSET} if waiting else names)


def load(
    deferred: Sequence[Toolset],
    holds: Collection[str],
    waiting: Collection[str],
    loaded: Sequence[ToolsetSwitch],
    call_input: Mapping[str, Any],
) -> Load:
    """The load a call asks for. It adds the toolset's tools the session may
    call that its registry does not hold yet, sorted by name; a session
    cut to fewer tools than its kind's adds only its own. A toolset the
    kind does not defer is invalid input, naming those it does."""
    try:
        name = LoadToolsetInput.model_validate(call_input).toolset
    except ValidationError as refused:
        raise ToolFailed(ToolFailure.INVALID_INPUT, _names(deferred, str(refused))) from None
    toolset = next((each for each in deferred if each.name == name), None)
    if toolset is None:
        raise ToolFailed(ToolFailure.INVALID_INPUT, _names(deferred, f"no toolset {name} to load"))
    mine = [tool for tool in toolset.tools if tool in holds]
    if not mine:
        return Load(text=f"The toolset {name} adds nothing: none of its tools is yours to call.")
    now = callable_tools(holds, waiting, loaded)
    added = tuple(sorted(tool for tool in mine if tool not in now))
    if not added:
        return Load(text=f"The toolset {name} adds nothing: its tools are already yours.")
    tools = ", ".join(added)
    return Load(
        text=f"Loaded the toolset {name}: {tools} are yours to call from your next turn on.",
        switch=ToolsetSwitch(name=name, version=toolset.version, tools=added),
    )


def _names(deferred: Sequence[Toolset], why: str) -> str:
    names = ", ".join(toolset.name for toolset in deferred) or "none"
    return f"{why}. The toolsets you may load: {names}."


class LoadToolsetToolImpl(ToolInterface):
    """Its spec puts it in a registry and its calls under policy, as reading
    the session's own records; the loop answers its calls (`load`), so
    `run` is never reached."""

    def __init__(self) -> None:
        self._spec = ToolSpec(
            name=LOAD_TOOLSET,
            description=DESCRIPTION,
            input_model=LoadToolsetInput,
            output_model=ToolsetLoaded,
            timeout=timedelta(seconds=10),
            authorization_class=ToolClass.READ,
            effect=Effect.IDEMPOTENT,
            interruptible=True,
            mode=ToolMode.SYNC,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def target(self, ctx: TenantContext, call_input: ToolInput) -> Target:
        return Target()

    async def preflight(
        self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime
    ) -> None:
        return None

    async def run(
        self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime
    ) -> Platform:
        raise ToolFailed(ToolFailure.PERMANENT, f"the loop answers {LOAD_TOOLSET}")
