"""A loop over the memory storage, the scripted provider of each provider, a
fake clock, and a sleep that only moves it: what the loop suites share.
Nothing here reaches a network, and no case waits on the wall clock."""

import asyncio
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

from acme.infra.impl.local import InfraLocalImpl
from acme.infra.outages import OutageSignalInterface
from acme.integrations.identity.absent import IdentityProviderAbsentImpl
from acme.integrations.impl.configured import IntegrationsOverImpl
from acme.integrations.model_providers.calls import ModelReply
from acme.integrations.model_providers.registry import ModelProvidersOverImpl
from acme.integrations.model_providers.scripted import ModelProviderScriptedImpl, ScriptedFailure
from acme.integrations.model_providers.types import ErrorKind, ProviderName, StopReason, Usage
from acme.om.agents.impl.loop import LoopManagerImpl, LoopOptions
from acme.om.agents.impl.sink import StreamSinkMemoryImpl
from acme.om.agents.types.kind import AgentKind, AgentKindCatalog, DoneRule, TreeLimits
from acme.om.agents.types.request import Start
from acme.om.agents.types.result import Claim
from acme.om.agents.types.run import RunEnd
from acme.om.attribution.types.authority import AuthorityMode
from acme.om.attribution.types.principal import Principal, PrincipalKind
from acme.om.base import Platform, new_id, utcnow
from acme.om.context import CredentialKind, RequestContext, Role, TenantContext, build_context
from acme.om.root import Managers, build_managers, engine_tools
from acme.om.steps.types.content import TextBlock, ToolUseBlock
from acme.om.steps.types.header import LoopOutcome, ParkReason
from acme.om.steps.types.step import Step, StepType
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.om.storage.root import StorageInterface
from acme.om.tenancy.rules import permissions_of
from acme.om.tools.attachments import AttachmentReaderInterface
from acme.om.tools.impl.attachments import AttachmentReaderNullImpl
from acme.om.tools.tool import ToolInterface, ToolRuntime
from acme.om.tools.types.policy import Decision, PolicyLayer, PolicyRule, Target
from acme.om.tools.types.tool import Effect, ToolClass, ToolInput, ToolSpec
from acme.om.windows.impl.gate import CallGateBudgetImpl
from contracts.doubles import APP, context
from contracts.factories import make_org
from contracts.step_storage import make_message

SONNET = "claude-sonnet-5-5"
"""The main role's model in the resolver's default table."""

ALLOWED = PolicyLayer(
    rules=(
        PolicyRule(authorization_class=ToolClass.READ, decision=Decision.ALLOW),
        PolicyRule(authorization_class=ToolClass.WRITE, decision=Decision.ALLOW),
        PolicyRule(authorization_class=ToolClass.INTEGRATION, decision=Decision.ALLOW),
    )
)


class Asked(ToolInput):
    q: str = "the total"


class Found(Platform):
    text: str


class Lookup(ToolInterface):
    """A read-only lookup that touches no workspace. It keeps who it ran as,
    and holds its first call until `release` is set when `holds` is. When
    `remote` is, it reads its target from the system it acts on, an await
    that suspends."""

    def __init__(
        self,
        name: str = "lookup",
        *,
        effect: Effect = Effect.READ_ONLY,
        authorization_class: str = ToolClass.READ,
        holds: bool = False,
    ) -> None:
        self._spec = ToolSpec(
            name=name,
            description=f"The {name} tool.",
            input_model=Asked,
            output_model=Found,
            timeout=timedelta(minutes=1),
            authorization_class=authorization_class,
            effect=effect,
            interruptible=True,
        )
        self.holds = holds
        self.remote = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.ran_as: list[UUID] = []

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def target(self, ctx: TenantContext, call_input: ToolInput) -> Target:
        if self.remote:
            await asyncio.sleep(0)
        return Target()

    async def preflight(
        self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime
    ) -> None:
        return None

    async def run(
        self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime
    ) -> Platform:
        assert isinstance(call_input, Asked)
        self.ran_as.append(ctx.user_id)
        if self.holds and len(self.ran_as) == 1:
            self.started.set()
            await self.release.wait()
        return Found(text=f"{self._spec.name} found {call_input.q}")


class Submitted(ToolInput):
    claim: Claim
    evidence: tuple[UUID, ...] = ()


class Submit(Lookup):
    """A delivery kind's result tool: what the model submits is judged by the
    loop, and this tool never runs."""

    def __init__(self) -> None:
        super().__init__("submit")
        self._spec = self._spec.model_copy(update={"input_model": Submitted})


ASSISTANT = AgentKind(
    name="assistant",
    version=1,
    tools=("lookup", "slow", "note", "send"),
    done_rule=DoneRule.ANSWER,
    authority=AuthorityMode.DELEGATED,
    tree=TreeLimits(height=2, count=4),
    prompts=("You answer questions about the records.",),
    policy=ALLOWED,
)
DELIVERY = AgentKind(
    name="delivery",
    version=1,
    tools=("lookup", "submit", "send"),
    done_rule=DoneRule.RESULT_TOOL,
    result_tool="submit",
    max_nudges=3,
    authority=AuthorityMode.STEADY,
    tree=TreeLimits(height=2, count=4),
    prompts=("You deliver the work, and submit it with its evidence.",),
    policy=ALLOWED,
)

HELPER = AgentKind(
    name="helper",
    version=1,
    tools=("lookup", "ask_person", "write_plan", "read_attachment"),
    done_rule=DoneRule.ANSWER,
    authority=AuthorityMode.DELEGATED,
    tree=TreeLimits(height=1, count=0),
    prompts=("You work on the records, and ask your person what you cannot find.",),
    policy=ALLOWED,
)
"""A kind that names the engine's own tools."""


class Clock:
    def __init__(self) -> None:
        self.now = utcnow()

    def __call__(self) -> datetime:
        return self.now


async def live(rctx: RequestContext, org_id: UUID, principal: Principal) -> TenantContext:
    """The adopter's transition: every principal still holds a member's
    place in the tenant."""
    return build_context(
        rctx,
        user_id=principal.id,
        org_id=org_id,
        role=Role.MEMBER,
        permissions=permissions_of(Role.MEMBER),
        credential_kind=CredentialKind.SESSION_TOKEN,
    )


def tools() -> dict[str, Lookup]:
    return {
        "lookup": Lookup(),
        "slow": Lookup("slow", holds=True),
        "note": Lookup("note", effect=Effect.UNSAFE, authorization_class=ToolClass.WRITE),
        "send": Lookup("send", effect=Effect.IDEMPOTENT, authorization_class=ToolClass.INTEGRATION),
        "submit": Submit(),
    }


@dataclass
class Loop:
    infra: InfraLocalImpl
    storage: StorageInterface
    managers: Managers
    loops: LoopManagerImpl
    anthropic: ModelProviderScriptedImpl
    openai: ModelProviderScriptedImpl
    sink: StreamSinkMemoryImpl
    clock: Clock
    owner: TenantContext
    tools: dict[str, Lookup]

    async def start(self, kind: str = "assistant") -> UUID:
        session = await self.managers.agents.start_session(
            self.owner, Start(id=new_id(), kind=kind, title="a question")
        )
        return session.id

    async def say(self, session_id: UUID, text: str, ctx: TenantContext | None = None) -> Step:
        """A principal's message, durable when this returns."""
        ctx = ctx or self.owner
        person = Principal(kind=PrincipalKind.PERSON, id=ctx.user_id)
        message = make_message(session_id, text, principal=person)
        (stored,) = await self.managers.steps.append_inputs(ctx, session_id, [message])
        return stored

    async def history(self, session_id: UUID) -> list[Step]:
        steps: list[Step] = []
        while True:
            after = steps[-1].seq if steps else 0
            page = await self.managers.steps.get_steps(self.owner, session_id, after, 200)
            steps.extend(page.items)
            if not page.has_more or not page.items:
                return steps

    def colleague(self) -> TenantContext:
        """A second member of the owner's tenant."""
        return build_context(
            RequestContext(request_id=new_id(), app=APP),
            user_id=new_id(),
            org_id=self.owner.org_id,
            role=Role.MEMBER,
            permissions=permissions_of(Role.MEMBER),
            credential_kind=CredentialKind.SESSION_TOKEN,
        )


def loop_over(
    tmp_path: Path,
    *,
    outages: OutageSignalInterface | None = None,
    options: LoopOptions | None = None,
    kinds: tuple[AgentKind, ...] = (ASSISTANT, DELIVERY),
    storage: StorageInterface | None = None,
    owner: TenantContext | None = None,
    sink: StreamSinkMemoryImpl | None = None,
    jitter: Callable[[], float] = random.random,
    reader: AttachmentReaderInterface | None = None,
) -> Loop:
    """`storage` None is the memory storage, and `owner` None a fresh
    tenant's owner; a suite over Postgres hands in both. `jitter` is what
    the loop draws its retry waits from. The loop's catalog holds the
    engine's tools before the suite's, over `reader`, None the null."""
    infra = InfraLocalImpl(tmp_path)
    anthropic = ModelProviderScriptedImpl(ProviderName.ANTHROPIC)
    openai = ModelProviderScriptedImpl(ProviderName.OPENAI)
    providers = ModelProvidersOverImpl(
        {ProviderName.ANTHROPIC: anthropic, ProviderName.OPENAI: openai}
    )
    integrations = IntegrationsOverImpl(IdentityProviderAbsentImpl(), providers)
    catalog = tools()
    storage = storage or StorageMemoryImpl()
    reader = reader or AttachmentReaderNullImpl()
    managers = build_managers(
        storage,
        infra,
        integrations=integrations,
        agent_kinds=kinds,
        principal_context=live,
        tool_catalog=tuple(catalog.values()),
        attachment_reader=reader,
    )
    clock = Clock()

    async def sleep(seconds: float) -> None:
        clock.now += timedelta(seconds=seconds)
        await asyncio.sleep(0)

    sink = sink or StreamSinkMemoryImpl()
    loops = LoopManagerImpl(
        managers.steps,
        managers.agent_sessions,
        managers.agents,
        AgentKindCatalog(kinds=kinds),
        managers.attribution,
        managers.models,
        managers.windows,
        managers.tools,
        CallGateBudgetImpl(managers.budget_gate, managers.pricing, managers.agent_sessions),
        providers,
        outages or infra.get_outages(),
        sink,
        engine_tools(managers.steps, reader) + tuple(catalog.values()),
        options or LoopOptions(control_poll=timedelta(milliseconds=1)),
        clock,
        sleep,
        jitter=jitter,
    )
    owner = owner or context(Role.OWNER, make_org())
    return Loop(infra, storage, managers, loops, anthropic, openai, sink, clock, owner, catalog)


def reply(*blocks: TextBlock | ToolUseBlock, model: str = SONNET) -> ModelReply:
    """A whole reply: a tool use stops it for the tool, text alone ends the
    turn."""
    calls = any(isinstance(block, ToolUseBlock) for block in blocks)
    return ModelReply(
        blocks=blocks,
        stop_reason=StopReason.TOOL_USE if calls else StopReason.END_TURN,
        usage=Usage(input=120, output=30),
        model=model,
    )


def use(name: str, q: str = "the total", use_id: str | None = None) -> ToolUseBlock:
    return ToolUseBlock(id=use_id or f"use_{name}_{new_id().hex[:8]}", name=name, input={"q": q})


def call(name: str, **call_input: object) -> ToolUseBlock:
    """A call of any tool, with the input the model wrote."""
    return ToolUseBlock(id=f"use_{name}_{new_id().hex[:8]}", name=name, input=call_input)


def said(text: str) -> TextBlock:
    return TextBlock(text=text)


async def outage_parks_at_once_and_resumes_at_the_retry_time(loop: Loop) -> None:
    """Shared with the suite over Valkey: one session learns the provider is
    failing; a second parks before it calls, and wakes at the retry time."""
    learner = await loop.start()
    await loop.say(learner, "What is the total?")
    overloaded = ScriptedFailure(kind=ErrorKind.OVERLOADED, retry_after=2)
    loop.anthropic.add(overloaded, overloaded, overloaded)
    loop.openai.add(reply(said("The total is 12."), model="gpt-6.1-sol"))

    learned = await loop.loops.run(loop.owner, learner)

    assert learned.outcome is LoopOutcome.SUCCEEDED, "it fell back to its declared fallback"
    assert len(loop.anthropic.calls) == 3, "two retries in process, then the outage"

    second = await loop.start()
    await loop.say(second, "And the average?")
    parked = await loop.loops.run(loop.owner, second)

    assert parked.end is RunEnd.PARKED and parked.park is not None
    assert parked.park.reason is ParkReason.PROVIDER and parked.park.unlock == "anthropic"
    assert parked.park.retry_at is not None and parked.park.retry_at > loop.clock()
    assert len(loop.anthropic.calls) == 3, "parked at once: no call, no retry"
    assert [s for s in await loop.history(second) if s.type is StepType.MODEL_REQUEST] == []

    loop.clock.now = parked.park.retry_at
    await loop.managers.agent_sessions.wake_session(loop.owner, second, parked.park)
    loop.anthropic.add(reply(said("The average is 3.")))
    resumed = await loop.loops.run(loop.owner, second)

    assert resumed.outcome is LoopOutcome.SUCCEEDED
    types = [step.type for step in await loop.history(second)]
    assert types.count(StepType.RESUMED) == 1 and len(loop.anthropic.calls) == 4
