"""The engine as the scaffold builds it, in this process: the memory
storage, the local infra under a temporary folder, and the scripted twin
as every model provider. Nothing here reads a key or opens a connection.

`Engine` is the driver's handle on it: it starts a session, speaks as
its person, runs a loop the way a runner does, and reads back what the
engine recorded: the steps, the usage records, the holds, the budget,
and the writer epoch.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from acme.infra.impl.local import InfraLocalImpl
from acme.integrations.identity.absent import IdentityProviderAbsentImpl
from acme.integrations.impl.configured import IntegrationsOverImpl
from acme.integrations.model_providers.calls import ModelCall, StreamPart
from acme.integrations.model_providers.registry import ModelProvidersOverImpl
from acme.integrations.model_providers.scripted import ModelProviderScriptedImpl, Turn
from acme.integrations.model_providers.types import ProviderName
from acme.om.agent_root import AgentRootOptions
from acme.om.agent_sessions.types.agent_session import AgentSession
from acme.om.agent_steps.types.agent_step import Actor, AgentStep, AgentStepType, Origin
from acme.om.agent_steps.types.content import Content, TextBlock
from acme.om.agent_steps.types.header import InputHeader, ModelRequestHeader, ParkReason
from acme.om.agents.types.kind import AgentKind, DoneRule
from acme.om.agents.types.request import Start
from acme.om.agents.types.run import LoopRun
from acme.om.attribution.types.authority import AuthorityMode
from acme.om.attribution.types.principal import Principal, PrincipalKind
from acme.om.base import new_id, utcnow
from acme.om.context import (
    AppContext,
    AppType,
    CredentialKind,
    RequestContext,
    Role,
    TenantContext,
    build_context,
)
from acme.om.root import build_managers
from acme.om.spend_budgets.types.amount import Amount
from acme.om.spend_budgets.types.spend_budget import (
    SpendBudget,
    SpendBudgetScopeKind,
    WindowKind,
)
from acme.om.spend_budgets.types.spend_hold import SpendTally
from acme.om.spend_budgets.types.usage import UsageRecord
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.om.tenancy.rules import permissions_of
from acme.om.tools.native.ask_person import ASK_PERSON
from acme.om.tools.native.spawn_sub_agent import SPAWN_SUB_AGENT
from acme.om.tools.native.wait_for_sub_agents import WAIT_FOR_SUB_AGENTS
from acme.om.tools.native.write_agent_plan import WRITE_AGENT_PLAN
from acme.om.tools.types.policy import Decision, ToolPolicyLayer, ToolPolicyRule
from acme.om.tools.types.tool import ToolClass
from dreamcatcher.workspace import Workspace, tools

ALLOWED = ToolPolicyLayer(
    rules=tuple(
        ToolPolicyRule(authorization_class=c, decision=Decision.ALLOW)
        for c in (ToolClass.READ, ToolClass.WRITE, ToolClass.EXECUTE, ToolClass.SPAWN)
    )
)

FIXER = AgentKind(
    name="fixer",
    version=1,
    tools=(
        WRITE_AGENT_PLAN,
        "read_file",
        "run_check",
        "edit_file",
        ASK_PERSON,
        SPAWN_SUB_AGENT,
        WAIT_FOR_SUB_AGENTS,
    ),
    done_rule=DoneRule.ANSWER,
    authority=AuthorityMode.DELEGATED,
    prompts=("You fix a failing check in a small workspace, and keep it green.",),
    policy=ALLOWED,
)
REVIEWER = AgentKind(
    name="reviewer",
    version=1,
    tools=("read_file", "run_check"),
    done_rule=DoneRule.ANSWER,
    authority=AuthorityMode.DELEGATED,
    share=Amount(cost_micros=1_000_000),
    prompts=("You review one part of a change, and report what holds.",),
    policy=ALLOWED,
)


class Twin(ModelProviderScriptedImpl):
    """The scripted twin, paced: it waits between the parts it streams, so
    a person can follow the run. It reads no key and opens no connection."""

    def __init__(self, provider: ProviderName, pause: float) -> None:
        super().__init__(provider)
        self.pause = pause

    async def stream(self, call: ModelCall, *, credential: object = None) -> AsyncIterator[StreamPart]:
        async for part in super().stream(call):
            if self.pause:
                await asyncio.sleep(self.pause)
            yield part


async def as_member(rctx: RequestContext, org_id: UUID, principal: Principal) -> TenantContext:
    """Who a tool call runs as: the principal, as a member of the tenant."""
    return build_context(
        rctx,
        user_id=principal.id,
        org_id=org_id,
        role=Role.MEMBER,
        permissions=permissions_of(Role.MEMBER),
        credential_kind=CredentialKind.AUTH_SESSION_TOKEN,
    )


@dataclass(frozen=True)
class Record:
    """Everything the engine recorded that a frame reads: each session's
    steps, the usage records, the holds the requests name, the budget and
    its tally, and the writer epoch of the root."""

    root: UUID
    steps: dict[UUID, list[AgentStep]]
    children: list[AgentSession]
    usage: list[UsageRecord]
    holds: dict[UUID, int]
    budget: SpendBudget | None
    tally: SpendTally | None
    epoch: int


class Engine:
    def __init__(self, folder: Path, pause: float, workspace: Workspace) -> None:
        self.workspace = workspace
        self.twins = {p: Twin(p, pause) for p in ProviderName}
        self.providers = ModelProvidersOverImpl(self.twins)
        self.storage = StorageMemoryImpl()
        self.managers = build_managers(
            self.storage,
            InfraLocalImpl(folder),
            integrations=IntegrationsOverImpl(IdentityProviderAbsentImpl(), self.providers),
            agent_root_options=AgentRootOptions(
                agent_kinds=(FIXER, REVIEWER),
                principal_context=as_member,
                tool_catalog=tools(workspace),
            ),
        )
        self.owner = build_context(
            RequestContext(request_id=new_id(), app=AppContext(type=AppType.CLI, version="dreamcatcher@1")),
            user_id=new_id(),
            org_id=new_id(),
            role=Role.OWNER,
            permissions=permissions_of(Role.OWNER),
            credential_kind=CredentialKind.AUTH_SESSION_TOKEN,
        )
        self.budget_id: UUID | None = None

    def script(self, turns: dict[ProviderName, list[Turn]]) -> None:
        for provider, more in turns.items():
            self.twins[provider].add(*more)

    async def start(self, kind: str, title: str) -> UUID:
        session = await self.managers.agents.start_agent_session(self.owner, Start(id=new_id(), kind=kind, title=title))
        return session.id

    async def say(self, agent_session_id: UUID, text: str) -> AgentStep:
        """The person's message, durable when this returns."""
        step_id = new_id()
        message = AgentStep(
            id=step_id,
            created_at=utcnow(),
            agent_session_id=agent_session_id,
            loop_id=step_id,
            type=AgentStepType.MESSAGE,
            actor=Actor.PERSON,
            origin=Origin.PORTAL,
            header=InputHeader(principal=Principal(kind=PrincipalKind.PERSON, id=self.owner.user_id)),
            content=Content(blocks=(TextBlock(text=text),)),
        )
        (stored,) = await self.managers.agent_steps.append_inputs(self.owner, agent_session_id, [message])
        return stored

    async def run(self, agent_session_id: UUID) -> LoopRun:
        """One run of the session's loop, as a runner makes it."""
        return await self.managers.loop.run(self.owner, agent_session_id)

    async def cap(self, root: UUID, cost_micros: int) -> SpendBudget:
        """A budget on the session's whole tree, for its life."""
        now = utcnow()
        budget = await self.managers.spend_budgets.create_spend_budget(
            self.owner,
            SpendBudget(
                id=new_id(),
                created_at=now,
                updated_at=now,
                created_by=self.owner.user_id,
                updated_by=self.owner.user_id,
                scope_kind=SpendBudgetScopeKind.TREE,
                scope_key=str(root),
                window_kind=WindowKind.LIFE,
                cost_micros=cost_micros,
            ),
        )
        self.budget_id = budget.id
        return budget

    async def raise_budget(self, cost_micros: int) -> int:
        """A raise, the instruction to continue: the budget's change queues a
        wake of every session parked on a budget, done here as the queue's
        worker would. Returns how many sessions it woke."""
        assert self.budget_id is not None
        budget = await self.managers.spend_budgets.get_spend_budget(self.owner, self.budget_id)
        await self.managers.spend_budgets.change_amount(self.owner, budget.id, Amount(cost_micros=cost_micros), budget.version)
        return await self.managers.agent_sessions.wake_parked(self.owner, ParkReason.SPEND_BUDGET)

    async def history(self, agent_session_id: UUID) -> list[AgentStep]:
        steps: list[AgentStep] = []
        while True:
            after = steps[-1].seq if steps else 0
            page = await self.managers.agent_steps.get_agent_steps(self.owner, agent_session_id, after, 200)
            steps.extend(page.items)
            if not page.has_more or not page.items:
                return steps

    async def children(self, agent_session_id: UUID) -> list[AgentSession]:
        page = await self.managers.agent_sessions.get_children(self.owner, agent_session_id, None, 50)
        return list(page.items)

    async def usage(self, agent_session_id: UUID) -> list[UsageRecord]:
        ledger = self.storage.get_ledger_storage()
        records: list[UsageRecord] = []
        while True:
            after = records[-1].id if records else None
            page = await ledger.read_usage_records(self.owner.org_id, agent_session_id, after, 200)
            records.extend(page)
            if len(page) < 200:
                return records

    async def epoch(self, agent_session_id: UUID) -> int:
        cursor = await self.managers.agent_steps.get_cursor(self.owner, agent_session_id)
        return cursor.epoch

    async def read(self, root: UUID) -> Record:
        """What the engine recorded, read back whole, each child's title
        opened, as the API opens a page of them."""
        children = await self.managers.agent_sessions.open_titles(self.owner, await self.children(root))
        sessions = [root, *(child.id for child in children)]
        steps = {sid: await self.history(sid) for sid in sessions}
        usage = [record for sid in sessions for record in await self.usage(sid)]
        ledger = self.storage.get_ledger_storage()
        holds: dict[UUID, int] = {}
        for step in (step for history in steps.values() for step in history):
            if isinstance(step.header, ModelRequestHeader) and step.header.spend_hold_id is not None:
                hold = await ledger.read_hold(self.owner.org_id, step.header.spend_hold_id)
                if hold is not None:
                    holds[hold.id] = hold.exposure.cost_micros or 0
        budget, tally = await self._budget()
        return Record(root, steps, children, usage, holds, budget, tally, await self.epoch(root))

    async def _budget(self) -> tuple[SpendBudget | None, SpendTally | None]:
        if self.budget_id is None:
            return None, None
        budget = await self.managers.spend_budgets.get_spend_budget(self.owner, self.budget_id)
        return budget, await self.managers.spend_budgets.get_spend(self.owner, self.budget_id)
