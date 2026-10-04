"""An agent kind: a versioned profile over the one loop. A product declares
its kinds; the engine holds them in a catalog, by name and version, and a
session pins the version it started on.

A kind names the tools it may call, the rule that says when a loop is
done, the result tool a delivery kind submits through, the authority mode
its calls run under, the bounds of a tree it roots, and the share one of
its sessions may spend when it runs as a sub-agent. It also carries
what the loop reads of it: its prompts, the model roles it calls, the
bounds of one loop, its layer of tool policy, the workspace its sessions
work in, and whether they hold private data. Its prompts and its tools'
contracts are the product's, versioned with it."""

from datetime import timedelta
from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from acme.infra.workspaces import EgressMode, EgressPolicy, IsolationMode, IsolationSpec
from acme.om.agent_sessions.limits import Limits
from acme.om.attribution.types.authority import AuthorityMode
from acme.om.attribution.types.principal import MAX_KIND
from acme.om.base import Platform
from acme.om.budgets.types.amount import Amount
from acme.om.exceptions import UnknownAgentKind
from acme.om.models.types.fill import MAIN, SUMMARIZER, ModelRole
from acme.om.tools.types.policy import PolicyLayer

NO_WORKSPACE = IsolationSpec(mode=IsolationMode.NONE, egress=EgressPolicy(mode=EgressMode.NONE))
"""The workspace of a kind that touches none: every transport refuses it."""


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
    # The bounds of one loop: the step guard, the error streak, the run time.
    limits: Limits = Limits()
    # Its layer of tool policy, before a tenant narrows or loosens it and
    # under the platform's ceilings.
    policy: PolicyLayer = PolicyLayer()
    # The workspace its sessions work in, prepared to this spec before a
    # loop's first model call and never weakened.
    isolation: IsolationSpec = NO_WORKSPACE
    # Whether its sessions hold private data, one of the rule of two's
    # three. Only a kind that reads no tenant record and no person's words
    # says False.
    private_data: bool = True

    @model_validator(mode="after")
    def _a_result_tool_is_one_it_calls(self) -> Self:
        if len(set(self.tools)) != len(self.tools):
            raise ValueError("a kind names each tool once")
        submits = self.done_rule is DoneRule.RESULT_TOOL
        if submits != (self.result_tool is not None):
            raise ValueError("a result-tool kind names its result tool, and no other kind does")
        if self.result_tool is not None and self.result_tool not in self.tools:
            raise ValueError("a kind's result tool is one of its tools")
        if MAIN not in self.roles:
            raise ValueError("a kind calls the main model role")
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
