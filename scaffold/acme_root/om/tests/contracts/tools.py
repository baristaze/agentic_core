"""Tools and helpers the tools suites share: a handful of tools in the
contract's shape, the tools manager over the memory storage with the
transport a case picks, and a tool call put in a session's history the way
the loop puts one there."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from acme.infra.secrets import SecretsInterface
from acme.infra.secrets.local import SecretsLocalImpl
from acme.infra.topics.memory import TopicsMemoryImpl
from acme.infra.transports import CommandSpec, SecretUse, SecretVia, TransportInterface
from acme.infra.transports.broker import BrokerTwinImpl
from acme.infra.transports.twin import TransportTwinImpl, TwinHandler, TwinReply
from acme.infra.workspaces import (
    EgressMode,
    EgressPolicy,
    IsolationMode,
    IsolationSpec,
    WorkspaceProviderInterface,
)
from acme.infra.workspaces.twin import WorkspaceTwinImpl
from acme.om.attribution import AttributionManagerInterface
from acme.om.attribution.types.authority import AuthorityMode, CallAuthority, CallReach
from acme.om.attribution.types.principal import AgentRef, Principal, PrincipalKind
from acme.om.base import Platform, new_id, utcnow
from acme.om.context import TenantContext
from acme.om.events import EventsManagerInterface
from acme.om.events.impl.manager import EventsManagerImpl, EventsOptions
from acme.om.exceptions import ToolFailed
from acme.om.outbox.impl.relay import OutboxRelayImpl
from acme.om.steps import StepsManagerInterface
from acme.om.steps.impl.manager import StepsManagerImpl, StepsOptions
from acme.om.steps.types.content import Content, TextBlock, ToolUseBlock
from acme.om.steps.types.header import ModelResponseHeader, ToolFailure, ToolResponseHeader
from acme.om.steps.types.step import Actor, Origin, Step, StepType
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.om.tools.impl.manager import ToolsManagerImpl, ToolsOptions
from acme.om.tools.registry import ToolRegistry
from acme.om.tools.rules import tool_request
from acme.om.tools.tool import ToolInterface, ToolRuntime
from acme.om.tools.types.policy import Decision, PolicyLayer, PolicyRule, Target
from acme.om.tools.types.tool import Effect, ToolClass, ToolInput, ToolMode, ToolSpec
from contracts.doubles import Members
from contracts.step_storage import a_person, make_message, make_request

TWIN_SPEC = IsolationSpec(mode=IsolationMode.TWIN, egress=EgressPolicy(mode=EgressMode.NONE))
HOST_SPEC = IsolationSpec(mode=IsolationMode.HOST, egress=EgressPolicy(mode=EgressMode.OPEN))


# The tools.


class CommandInput(ToolInput):
    argv: tuple[str, ...]


class CommandOutput(Platform):
    exit_code: int | None
    stdout: str
    stderr: str


class Command(ToolInterface):
    """Runs one command in the workspace, with the secrets it declares."""

    def __init__(
        self,
        name: str = "run_command",
        *,
        effect: Effect = Effect.IDEMPOTENT,
        timeout: timedelta = timedelta(minutes=1),
        secrets: tuple[SecretUse, ...] = (),
        authorization_class: str = ToolClass.EXECUTE,
        mode: ToolMode = ToolMode.SYNC,
    ) -> None:
        self._spec = ToolSpec(
            name=name,
            description="Runs a command in the workspace.",
            input_model=CommandInput,
            output_model=CommandOutput,
            timeout=timeout,
            authorization_class=authorization_class,
            effect=effect,
            interruptible=True,
            mode=mode,
            secrets=secrets,
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
        assert isinstance(call_input, CommandInput)
        result = await runtime.run(
            call_input.argv, secrets=tuple(use.name for use in self._spec.secrets)
        )
        return CommandOutput(exit_code=result.exit_code, stdout=result.stdout, stderr=result.stderr)


class PushInput(ToolInput):
    branch: str
    # What a model may fill in to vouch for its call; policy never reads it.
    protected: bool = False
    justification: str = ""


class Pushed(Platform):
    branch: str


class PushBranch(ToolInterface):
    """Pushes a branch. Whether the branch is protected is read from the
    source control it pushes to, `protections`, never from the input."""

    def __init__(self, protections: Mapping[str, bool]) -> None:
        self._protections = protections
        self.pushed: list[str] = []
        self._spec = ToolSpec(
            name="push_branch",
            description="Pushes a branch to the bound repository.",
            input_model=PushInput,
            output_model=Pushed,
            timeout=timedelta(minutes=1),
            authorization_class=ToolClass.INTEGRATION,
            effect=Effect.UNSAFE,
            interruptible=False,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def target(self, ctx: TenantContext, call_input: ToolInput) -> Target:
        assert isinstance(call_input, PushInput)
        protected = self._protections.get(call_input.branch, True)
        return Target(kind="branch", attributes={"protected": protected})

    async def preflight(
        self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime
    ) -> None:
        assert isinstance(call_input, PushInput)
        if call_input.branch not in self._protections:
            raise ToolFailed(ToolFailure.INVALID_INPUT, f"no branch {call_input.branch!r}")

    async def run(
        self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime
    ) -> Platform:
        assert isinstance(call_input, PushInput)
        self.pushed.append(call_input.branch)
        return Pushed(branch=call_input.branch)


KIND_DEFAULTS = PolicyLayer(
    rules=(
        PolicyRule(authorization_class=ToolClass.READ, decision=Decision.ALLOW),
        PolicyRule(authorization_class=ToolClass.EXECUTE, decision=Decision.ALLOW),
        PolicyRule(authorization_class=ToolClass.INTEGRATION, decision=Decision.APPROVE),
        PolicyRule(tool="push_branch", target={"protected": True}, decision=Decision.DENY),
    )
)
"""An agent kind's defaults, as the suites use them."""

INJECTED_TOKEN = SecretUse(name="api_token", via=SecretVia.INJECTED, env="API_TOKEN")
BROKERED_TOKEN = SecretUse(
    name="repo_token", via=SecretVia.BROKERED, destination="git.example.test"
)


# The manager.


class Clock:
    """A clock a case moves."""

    def __init__(self) -> None:
        self.now = utcnow()

    def __call__(self) -> datetime:
        return self.now


class Answering(AttributionManagerInterface):
    """Just enough attribution for the gate: every call runs under the
    person who asks, in the context that asks, and the rule of two holds
    when `needs_person` says so. A partial double: only `authorize_call` is
    reached, so the abstract set is cleared below."""

    def __init__(self) -> None:
        self.needs_person = False
        self.reaches: list[CallReach] = []

    async def authorize_call(
        self, ctx: TenantContext, session_id: UUID, reach: CallReach
    ) -> CallAuthority:
        self.reaches.append(reach)
        return CallAuthority(
            principal=Principal(kind=PrincipalKind.PERSON, id=ctx.user_id),
            mode=AuthorityMode.STEADY,
            context=ctx,
            needs_person=self.needs_person,
        )


Answering.__abstractmethods__ = frozenset()


@dataclass
class Tools:
    manager: ToolsManagerImpl
    steps: StepsManagerInterface
    events: EventsManagerInterface
    storage: StorageMemoryImpl
    clock: Clock
    attribution: Answering


def tools_over(
    transport: TransportInterface,
    workspaces: WorkspaceProviderInterface | None = None,
    options: ToolsOptions | None = None,
) -> Tools:
    storage = StorageMemoryImpl()
    members = Members()  # pyright: ignore[reportAbstractUsage] (a partial double)
    steps = StepsManagerImpl(storage.get_step_storage(), members, StepsOptions())
    events = EventsManagerImpl(storage.get_event_storage(), members, EventsOptions())
    relay = OutboxRelayImpl(
        storage.get_outbox_storage(), storage.get_event_storage(), TopicsMemoryImpl()
    )
    clock = Clock()
    attribution = Answering()  # pyright: ignore[reportAbstractUsage] (a partial double)
    manager = ToolsManagerImpl(
        storage.get_tool_storage(),
        steps,
        members,
        events,
        relay,
        workspaces or WorkspaceTwinImpl(),
        transport,
        options or ToolsOptions(),
        clock,
        attribution=attribution,
    )
    return Tools(manager, steps, events, storage, clock, attribution)


def twin_transport(
    tmp_path: Path, secrets: SecretsInterface | None = None
) -> tuple[TransportTwinImpl, BrokerTwinImpl]:
    broker = BrokerTwinImpl()
    return TransportTwinImpl(secrets or SecretsLocalImpl(tmp_path / "secrets.env"), broker), broker


# A call in a session's history.


@dataclass
class Call:
    session_id: UUID
    epoch: int
    request: Step
    call_input: dict[str, Any]


async def put_call(
    steps: StepsManagerInterface,
    ctx: TenantContext,
    tool: str,
    call_input: dict[str, Any],
    authorization_class: str,
    session_id: UUID | None = None,
    epoch: int | None = None,
) -> Call:
    """A model's tool use and its request step in the history, written and
    numbered as the loop writes them: the request is persisted before any
    operation of the tools manager sees it."""
    session_id = session_id or new_id()
    if epoch is None:
        (message,) = await steps.append_inputs(ctx, session_id, [make_message(session_id)])
        epoch = await steps.begin_run(ctx, session_id)
        loop_id = message.id
    else:
        loop_id = (await steps.get_steps(ctx, session_id, 0, 1)).items[0].loop_id
    model_request = make_request(session_id, loop_id)
    use = ToolUseBlock(id=f"call_{new_id().hex[:8]}", name=tool, input=call_input)
    model_response = Step(
        id=new_id(),
        created_at=utcnow(),
        session_id=session_id,
        loop_id=loop_id,
        type=StepType.MODEL_RESPONSE,
        actor=Actor.MODEL,
        origin=Origin.ENGINE,
        responds_to=model_request.id,
        header=ModelResponseHeader(),
        content=Content(blocks=(use,)),
    )
    request = tool_request(
        new_id(),
        utcnow(),
        model_response,
        use,
        authorization_class,
        principal=a_person(),
        authority=AuthorityMode.STEADY,
        agent=AgentRef(kind="delivery", version=1, session_id=session_id),
    )
    stored = await steps.append_steps(
        ctx, session_id, epoch, [model_request, model_response, request]
    )
    return Call(session_id, epoch, stored[-1], call_input)


def registry_of(*tools: ToolInterface) -> ToolRegistry:
    return ToolRegistry(tools)


def echoing(env_name: str | None = None) -> TwinHandler:
    """A twin handler that answers a command with its argv joined, and the
    value of `env_name` in its environment after it when one is named."""

    async def handler(command: CommandSpec, env: Mapping[str, str]) -> TwinReply:
        out = " ".join(command.argv)
        if env_name is not None:
            out += f" {env.get(env_name, '')}"
        return TwinReply(exit_code=0, stdout=out)

    return handler


def result_text(response: Step) -> str:
    """The text a tool response gives the model."""
    return "\n".join(
        part.text for part in response.as_tool_response().parts if isinstance(part, TextBlock)
    )


def failure_of(response: Step) -> ToolFailure | None:
    header = response.header
    assert isinstance(header, ToolResponseHeader)
    return header.failure
