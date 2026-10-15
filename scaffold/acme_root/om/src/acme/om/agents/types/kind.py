"""An agent kind: a versioned profile over the one loop. A product declares
its kinds; the engine holds them in a catalog, by name and version, and a
session pins the version it started on.

A kind names the tools it may call, alone or in toolsets, some of which
it may defer until the agent loads them; the rule that says when a loop is
done, the result tool a delivery kind submits through, the authority mode
its calls run under, the bounds of a tree it roots, and the share one of
its sessions may spend when it runs as a sub-agent. It also carries
what the loop reads of it: its prompts, the model roles it calls and
what it asks of them, the bounds of one loop, its layer of tool policy, the workspace its sessions
work in, and whether they hold private data. Its prompts and its tools'
contracts are the product's, versioned with it."""

from collections.abc import Mapping
from datetime import timedelta
from enum import StrEnum
from typing import Any, Self

from pydantic import Field, model_validator

from acme.infra.agent_workspaces import EgressMode, EgressPolicy, IsolationMode, IsolationSpec
from acme.om.agent_sessions.limits import Limits
from acme.om.attribution.types.authority import AuthorityMode
from acme.om.attribution.types.principal import MAX_KIND
from acme.om.base import OmBase
from acme.om.exceptions import UnknownAgentKind
from acme.om.language_models.types.fill import MAIN, SUMMARIZER, ModelAsk, ModelRole
from acme.om.spend_budgets.types.amount import Amount
from acme.om.tools.types.policy import ToolPolicyLayer
from acme.om.tools.types.toolset import LOAD_TOOLSET, Toolset

NO_AGENT_WORKSPACE = IsolationSpec(
    mode=IsolationMode.NONE, egress=EgressPolicy(mode=EgressMode.NONE)
)
"""The workspace of a kind that touches none: every transport refuses it."""


class DoneRule(StrEnum):
    """When a loop of the kind is done."""

    ANSWER = "answer"  # an assistant: a turn with no tool call is the answer
    RESULT_TOOL = "result_tool"  # a delivery agent: only its result tool ends a loop


class TreeLimits(OmBase):
    """The bounds of a tree. Height 1 is a single agent, 2 lets the root have
    children that have none, and so on. Count is the most sub-agents the
    tree holds besides its root, and concurrency, when set, the most that
    run at once. Out of the box a tree is three levels deep, a root, its
    sub-agents, and theirs, and holds ten sub-agents."""

    height: int = Field(default=3, ge=1)
    count: int = Field(default=10, ge=0)
    concurrency: int | None = Field(default=None, ge=1)


class AgentKind(OmBase):
    name: str = Field(min_length=1, max_length=MAX_KIND)
    version: int = Field(ge=1)
    # Every tool it may call, by name: those it names alone, its toolsets',
    # and its deferred toolsets' with `load_toolset`, folded in when it is
    # built. Its registry renders them sorted by name, whatever their order
    # here; the tools that wait for a load (`waiting`) join it only then.
    tools: tuple[str, ...] = ()
    # The toolsets its registry holds from the start.
    toolsets: tuple[Toolset, ...] = ()
    # The toolsets it defers: each is one line in `load_toolset`'s
    # description until the agent loads it.
    deferred: tuple[Toolset, ...] = ()
    done_rule: DoneRule
    result_tool: str | None = None  # the tool a result-tool kind submits through
    max_nudges: int = Field(default=3, ge=1)  # turns that neither continue nor submit
    authority: AuthorityMode
    tree: TreeLimits = TreeLimits()  # a kind that names none takes the default
    # How long a tree the kind roots has, from its start: turned into one
    # instant then, never a duration per call. None is no deadline.
    deadline: timedelta | None = None
    # What one of its sessions may spend over its life when it runs as a
    # sub-agent: its share, a budget on its own session that its spawn
    # writes. The tree's budget still bounds it. A kind with none is never
    # spawned, so no child can spend all its tree has left.
    share: Amount | None = None
    # Its prompts, in order: the first layer of every request it renders.
    prompts: tuple[str, ...] = ()
    # The model roles it calls: its own turns, and the summarizer its
    # compaction calls.
    roles: tuple[ModelRole, ...] = (MAIN, SUMMARIZER)
    # What it asks of them, a role at most once: how much model a role needs
    # and how hard it works, never a model. A role it asks nothing of
    # resolves by its name alone.
    asks: tuple[ModelAsk, ...] = ()
    # The bounds of one loop: the step guard, the error streak, the run time.
    limits: Limits = Limits()
    # Its layer of tool policy, before a tenant narrows or loosens it and
    # under the platform's ceilings.
    policy: ToolPolicyLayer = ToolPolicyLayer()
    # The workspace its sessions work in, prepared to this spec before a
    # loop's first model call and never weakened.
    isolation: IsolationSpec = NO_AGENT_WORKSPACE
    # Whether its sessions hold private data, one of the rule of two's
    # three. Only a kind that reads no tenant record and no person's words
    # says False.
    private_data: bool = True

    @model_validator(mode="before")
    @classmethod
    def _its_toolsets_join_its_tools(cls, data: Any) -> Any:
        """The tools of every toolset it names, deferred ones included, and
        `load_toolset` when it defers one, join the tools it names alone, so
        `tools` is everything a session of it may come to call: what a
        principal must be allowed, what a child's tools are cut to, and
        what the rule of two reads. Folding a folded kind again changes
        nothing."""
        if not isinstance(data, Mapping):
            return data
        named = tuple(data.get("tools", ()))
        if len(set(named)) != len(named):
            raise ValueError("a kind names each tool once")
        held: list[str] = []
        for toolset in (*data.get("toolsets", ()), *data.get("deferred", ())):
            held.extend(Toolset.model_validate(toolset).tools)
        load = (LOAD_TOOLSET,) if data.get("deferred") else ()
        return {**data, "tools": tuple(dict.fromkeys((*named, *held, *load)))}

    @model_validator(mode="after")
    def _a_result_tool_is_one_it_calls(self) -> Self:
        names = [toolset.name for toolset in (*self.toolsets, *self.deferred)]
        if len(set(names)) != len(names):
            raise ValueError("a kind names each toolset once")
        if (LOAD_TOOLSET in self.tools) != bool(self.deferred):
            raise ValueError(f"a kind holds {LOAD_TOOLSET} when, and only when, it defers")
        submits = self.done_rule is DoneRule.RESULT_TOOL
        if submits != (self.result_tool is not None):
            raise ValueError("a result-tool kind names its result tool, and no other kind does")
        if self.result_tool is not None and self.result_tool not in self.tools:
            raise ValueError("a kind's result tool is one of its tools")
        if self.result_tool is not None and self.result_tool in self.waiting:
            raise ValueError("a kind's result tool never waits for a load")
        if MAIN not in self.roles:
            raise ValueError("a kind calls the main model role")
        asked = [ask.role for ask in self.asks]
        if len(set(asked)) != len(asked) or not set(asked) <= set(self.roles):
            raise ValueError("a kind asks once of a model role it calls")
        return self

    @property
    def waiting(self) -> frozenset[str]:
        """The tools that join its registry only when a load adds them: those
        of its deferred toolsets that no toolset it holds from the start
        holds. A tool it names alone that a deferred toolset holds waits
        too."""
        held = {tool for toolset in self.toolsets for tool in toolset.tools}
        return frozenset(
            tool for toolset in self.deferred for tool in toolset.tools if tool not in held
        )


class AgentKindCatalog(OmBase):
    """The kinds a product declares, every version it still runs: a session
    pins a version, and keeps it until it switches. The toolsets its kinds
    name are held beside them: a version of a toolset names one group of
    tools, whichever kind names it."""

    kinds: tuple[AgentKind, ...] = ()

    @model_validator(mode="after")
    def _one_kind_per_version(self) -> Self:
        named = {(kind.name, kind.version) for kind in self.kinds}
        if len(named) != len(self.kinds):
            raise ValueError("a catalog declares each version of a kind once")
        toolsets: dict[tuple[str, int], Toolset] = {}
        for kind in self.kinds:
            for toolset in (*kind.toolsets, *kind.deferred):
                known = toolsets.setdefault((toolset.name, toolset.version), toolset)
                if known.tools != toolset.tools or known.summary != toolset.summary:
                    raise ValueError(
                        f"toolset {toolset.name} at version {toolset.version} is declared twice"
                    )
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
