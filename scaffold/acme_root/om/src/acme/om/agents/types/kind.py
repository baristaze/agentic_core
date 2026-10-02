"""An agent kind: a versioned profile over the one loop. A product declares
its kinds; the engine holds them in a catalog, by name and version, and a
session pins the version it started on.

A kind names the tools it may call, the rule that says when a loop is
done, the result tool a delivery kind submits through, the authority mode
its calls run under, and the bounds of a tree it roots. Its prompts and
its tools' contracts are the product's, versioned with it."""

from datetime import timedelta
from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from acme.om.attribution.types.authority import AuthorityMode
from acme.om.attribution.types.principal import MAX_KIND
from acme.om.base import Platform
from acme.om.exceptions import UnknownAgentKind


class DoneRule(StrEnum):
    """When a loop of the kind is done."""

    ANSWER = "answer"  # an assistant: a turn with no tool call is the answer
    RESULT_TOOL = "result_tool"  # a delivery agent: only its result tool ends a loop


class TreeLimits(Platform):
    """The bounds of a tree. Height 1 is a single agent, 2 lets the root have
    children that have none, and so on. Count is the most sub-agents the
    tree holds besides its root, and concurrency, when set, the most that
    run at once."""

    height: int = Field(ge=1)
    count: int = Field(ge=0)
    concurrency: int | None = Field(default=None, ge=1)


class AgentKind(Platform):
    name: str = Field(min_length=1, max_length=MAX_KIND)
    version: int = Field(ge=1)
    # Its registry: the tools it may call, by name, in the order they render.
    tools: tuple[str, ...] = ()
    done_rule: DoneRule
    result_tool: str | None = None  # the tool a result-tool kind submits through
    max_nudges: int = Field(default=3, ge=1)  # turns that neither continue nor submit
    authority: AuthorityMode
    tree: TreeLimits
    # How long a tree the kind roots has, from its start: turned into one
    # instant then, never a duration per call. None is no deadline.
    deadline: timedelta | None = None

    @model_validator(mode="after")
    def _a_result_tool_is_one_it_calls(self) -> Self:
        if len(set(self.tools)) != len(self.tools):
            raise ValueError("a kind names each tool once")
        submits = self.done_rule is DoneRule.RESULT_TOOL
        if submits != (self.result_tool is not None):
            raise ValueError("a result-tool kind names its result tool, and no other kind does")
        if self.result_tool is not None and self.result_tool not in self.tools:
            raise ValueError("a kind's result tool is one of its tools")
        return self


class AgentKindCatalog(Platform):
    """The kinds a product declares, every version it still runs: a session
    pins a version, and keeps it until it switches."""

    kinds: tuple[AgentKind, ...] = ()

    @model_validator(mode="after")
    def _one_kind_per_version(self) -> Self:
        named = {(kind.name, kind.version) for kind in self.kinds}
        if len(named) != len(self.kinds):
            raise ValueError("a catalog declares each version of a kind once")
        return self

    def get(self, name: str, version: int) -> AgentKind:
        for kind in self.kinds:
            if kind.name == name and kind.version == version:
                return kind
        raise UnknownAgentKind(f"no agent kind {name} at version {version}")

    def latest(self, name: str) -> AgentKind:
        versions = [kind for kind in self.kinds if kind.name == name]
        if not versions:
            raise UnknownAgentKind(f"no agent kind {name}")
        return max(versions, key=lambda kind: kind.version)
