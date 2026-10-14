"""The loop's hooks: where a layer above the engine extends a run without
editing the loop. A root wires one impl; the null one extends nothing.

Two seams, each asked once a run. Before anything renders, the toolsets a
session holds beyond its kind's: a tenant's own connector, say, which
rides on the session and never on the kind, the product's one version
shared by every tenant. The loop folds them into the session's kind, so
held ones join its registry and deferred ones its `load_toolset` line,
and a load of one is recorded as any load is. Then, once the workspace is
prepared and before the first model call, the steps a layer appends: what
changed under the model since its last loop, say. They are written under
the run's epoch, so a run that lost its claim writes none."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from acme.infra.agent_workspaces import AgentWorkspace
from acme.om.agent_sessions.types.agent_session import AgentSession
from acme.om.agent_steps.types.agent_step import AgentStep
from acme.om.agents.types.kind import AgentKind
from acme.om.context import TenantContext
from acme.om.tools.tool import ToolInterface
from acme.om.tools.types.toolset import LOAD_TOOLSET, Toolset


@dataclass(frozen=True)
class AttachedToolsets:
    """The toolsets a session holds beyond its kind's, and a tool for each
    name they hold. `held` joins the registry from the start; `deferred`
    waits for a load. Each tool is named by exactly one of them."""

    held: tuple[Toolset, ...]
    deferred: tuple[Toolset, ...]
    tools: tuple[ToolInterface, ...]

    def __post_init__(self) -> None:
        named = [tool for toolset in (*self.held, *self.deferred) for tool in toolset.tools]
        given = [tool.spec.name for tool in self.tools]
        if len(set(named)) != len(named) or sorted(named) != sorted(given):
            raise ValueError("an attached toolset's tools are given once, each by one toolset")

    @property
    def names(self) -> tuple[str, ...]:
        """Every tool a session may come to call through them, `load_toolset`
        included when one is deferred."""
        load = (LOAD_TOOLSET,) if self.deferred else ()
        return (*(tool.spec.name for tool in self.tools), *load)


NO_ATTACHED_TOOLSETS = AttachedToolsets(held=(), deferred=(), tools=())


def with_attached_toolsets(kind: AgentKind, attached: AttachedToolsets) -> AgentKind:
    """`kind` with the attached toolsets folded in, as a kind declared with
    them would be: its tools, its waiting tools, and its `load_toolset`
    line. Nothing attached is the kind itself."""
    if attached == NO_ATTACHED_TOOLSETS:
        return kind
    return AgentKind.model_validate(
        {
            **dict(kind),
            "toolsets": (*kind.toolsets, *attached.held),
            "deferred": (*kind.deferred, *attached.deferred),
        }
    )


@dataclass(frozen=True)
class LoopStart:
    """A run as it reaches its first model call: its session, its kind with
    the attached toolsets folded in, its epoch and loop, whether the loop is
    new or one the run takes up again, and its prepared workspace."""

    agent_session: AgentSession
    kind: AgentKind
    epoch: int
    loop_id: UUID
    new_loop: bool
    workspace: AgentWorkspace


class LoopHooksInterface(ABC):
    @abstractmethod
    async def attached_toolsets(
        self, ctx: TenantContext, agent_session: AgentSession
    ) -> AttachedToolsets:
        """What `agent_session` holds beyond its kind: the same answer every
        time it is asked for the same session, so its tool definitions keep
        their bytes and a cached prefix holds. None of its toolsets shares a
        name with one its kind names, and none of its tools a name with a
        tool of the catalog."""
        ...

    @abstractmethod
    async def before_first_call(self, ctx: TenantContext, start: LoopStart) -> Sequence[AgentStep]:
        """The steps appended before the run's first model call, in order,
        after its workspace is prepared. A refusal of the session's fill
        (`UnresolvedRole`, `UnpricedModel`) ends the loop errored, as the
        loop's own resolve does."""
        ...
