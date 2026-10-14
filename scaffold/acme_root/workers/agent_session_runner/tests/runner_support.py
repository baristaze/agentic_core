"""What the runner suites share: a kind and a tool in the contract's shape,
as a product declares them, and the turns of a scripted model.

The kind is an assistant that runs commands in its workspace; the tool runs
one command through the call's runtime and answers what it printed. Its
effect is `unsafe`, so a call a lost run left open is settled from the
transport's record and never run again."""

from datetime import timedelta
from uuid import uuid4

from acme.infra.agent_workspaces import EgressMode, EgressPolicy, IsolationMode, IsolationSpec
from acme.integrations.model_providers.calls import ModelReply
from acme.integrations.model_providers.content import TextBlock, ToolUseBlock
from acme.integrations.model_providers.types import StopReason, Usage
from acme.om.agents.types.kind import NO_AGENT_WORKSPACE, AgentKind, DoneRule, TreeLimits
from acme.om.attribution.types.authority import AuthorityMode
from acme.om.base import Platform
from acme.om.context import TenantContext
from acme.om.tools.native.write_agent_plan import WRITE_AGENT_PLAN
from acme.om.tools.tool import ToolInterface, ToolRuntime
from acme.om.tools.types.policy import Decision, Target, ToolPolicyLayer, ToolPolicyRule
from acme.om.tools.types.tool import Effect, ToolClass, ToolInput, ToolSpec
from acme.om.windows.rules import READ_TOOL_OUTPUT

HOST = IsolationSpec(mode=IsolationMode.HOST, egress=EgressPolicy(mode=EgressMode.OPEN))
"""A directory on this host, which confines files only: local and test."""

SONNET = "claude-sonnet-5-5"
"""The main role's model in the resolver's default table."""


class CommandInput(ToolInput):
    argv: tuple[str, ...]


class CommandOutput(Platform):
    exit_code: int | None
    stdout: str
    stderr: str


class RunCommand(ToolInterface):
    """Runs one command in the workspace and answers what it printed."""

    def __init__(self) -> None:
        self._spec = ToolSpec(
            name="run_command",
            description=(
                "Runs one command in the workspace, as an argument vector, and answers "
                "its exit code and what it printed."
            ),
            input_model=CommandInput,
            output_model=CommandOutput,
            timeout=timedelta(minutes=2),
            authorization_class=ToolClass.EXECUTE,
            effect=Effect.UNSAFE,
            interruptible=True,
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
        result = await runtime.run(call_input.argv)
        return CommandOutput(exit_code=result.exit_code, stdout=result.stdout, stderr=result.stderr)


def assistant(isolation: IsolationSpec = HOST) -> AgentKind:
    """An assistant that runs commands in its workspace, on each person's
    own permissions."""
    return AgentKind(
        name="assistant",
        version=1,
        tools=("run_command",),
        done_rule=DoneRule.ANSWER,
        authority=AuthorityMode.DELEGATED,
        tree=TreeLimits(height=1, count=0),
        prompts=(
            "You answer a person's question. Run a command in the workspace with "
            "run_command when the answer needs one, then answer in one sentence.",
        ),
        policy=ToolPolicyLayer(
            rules=(ToolPolicyRule(authorization_class=ToolClass.EXECUTE, decision=Decision.ALLOW),)
        ),
        isolation=isolation,
    )


def planning_assistant() -> AgentKind:
    """The assistant that also keeps a plan and reads a result kept whole
    outside its step back by its handle."""
    return AgentKind(
        name="planning_assistant",
        version=1,
        tools=("run_command", WRITE_AGENT_PLAN, READ_TOOL_OUTPUT),
        done_rule=DoneRule.ANSWER,
        authority=AuthorityMode.DELEGATED,
        tree=TreeLimits(height=1, count=0),
        prompts=(
            "You answer a person's question. Write your plan first, run a command in "
            "the workspace when the answer needs one, and read a kept result back by "
            "its handle.",
        ),
        policy=ToolPolicyLayer(
            rules=(
                ToolPolicyRule(authorization_class=ToolClass.EXECUTE, decision=Decision.ALLOW),
                ToolPolicyRule(authorization_class=ToolClass.READ, decision=Decision.ALLOW),
            )
        ),
        isolation=HOST,
    )


KINDS = (assistant(), planning_assistant())
TOOLS: tuple[ToolInterface, ...] = (RunCommand(),)
ABSENT = (assistant(NO_AGENT_WORKSPACE),)
"""The kind with no workspace, for a suite whose tool touches none."""


def runs(*argv: str) -> ModelReply:
    """A turn that asks for one command."""
    use = ToolUseBlock(id=f"use_{uuid4().hex[:12]}", name="run_command", input={"argv": argv})
    return ModelReply(
        blocks=(TextBlock(text="Let me look."), use),
        stop_reason=StopReason.TOOL_USE,
        usage=Usage(input=120, output=30),
        model=SONNET,
    )


def calls(name: str, **arguments: object) -> ModelReply:
    """A turn that asks for one call of the tool `name`."""
    use = ToolUseBlock(id=f"use_{uuid4().hex[:12]}", name=name, input=arguments)
    return ModelReply(
        blocks=(TextBlock(text="On it."), use),
        stop_reason=StopReason.TOOL_USE,
        usage=Usage(input=120, output=30),
        model=SONNET,
    )


def answers(text: str) -> ModelReply:
    """A turn that answers and calls nothing."""
    return ModelReply(
        blocks=(TextBlock(text=text),),
        stop_reason=StopReason.END_TURN,
        usage=Usage(input=160, output=20),
        model=SONNET,
    )
