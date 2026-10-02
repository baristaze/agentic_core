"""What a tool implements, and the one way it reaches a workspace.

A tool is native code, a server's tool over MCP, or a sub-agent; each is a
`ToolInterface` with a declared `ToolSpec`. A tool reaches the world only
through the `ToolRuntime` its call is given: the workspace's transport,
bound to the call's key, its run's writer epoch, its deadline, and the
secrets its spec declares. It never holds the transport or a workspace of
its own, and never starts a process or opens a file on the engine's host.

An `unsafe` tool runs at most one command a call, so the transport's record
of that command is the call's outcome after a crash. A tool that must do
more in one call does it in one command, or is not unsafe."""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from datetime import datetime
from uuid import UUID

from acme.infra.transports import (
    CommandResult,
    CommandSpec,
    FileEntry,
    OutputSink,
    SecretUse,
    TransportInterface,
)
from acme.infra.workspaces import Workspace
from acme.om.base import Platform
from acme.om.context import TenantContext
from acme.om.exceptions import ToolFailed
from acme.om.steps.types.header import ToolFailure
from acme.om.tools.rules import command_text
from acme.om.tools.types.call import JobHandle
from acme.om.tools.types.policy import Target
from acme.om.tools.types.tool import Effect, ToolInput, ToolSpec

SecretAudit = Callable[[SecretUse], Awaitable[None]]
"""Records that the call uses a secret, by name, before the command that
uses it runs."""


class ToolRuntime:
    """One call's way into its workspace. A preflight's is read-only."""

    def __init__(
        self,
        transport: TransportInterface,
        workspace: Workspace,
        *,
        key: UUID,
        epoch: int,
        deadline: datetime,
        effect: Effect,
        secrets: tuple[SecretUse, ...],
        audit: SecretAudit,
        on_output: OutputSink | None = None,
        read_only: bool = False,
        answer_chars: int = 50_000,
    ) -> None:
        self._answer_chars = answer_chars  # the bound of what the model reads of an answer
        self._transport = transport
        self._workspace = workspace
        self._key = key
        self._epoch = epoch
        self._effect = effect
        self._secrets = {use.name: use for use in secrets}
        self._audit = audit
        self._on_output = on_output
        self._read_only = read_only
        self._commands = 0
        self.deadline = deadline

    @property
    def key(self) -> UUID:
        """The call's idempotency key: the id of its request, the same on
        every run that repeats the call. A tool that reaches a system of its
        own, a server's tool or a job, passes it there, so a repeat after a
        crash does once what the first did; a command carries it already."""
        return self._key

    async def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: str = ".",
        env: tuple[tuple[str, str], ...] = (),
        secrets: tuple[str, ...] = (),
        max_output: int = 1_000_000,
    ) -> CommandResult:
        """Runs a command under the call's key, epoch, and deadline, with the
        declared secrets it names, each audited by name first. A non-zero
        exit is returned; a command that ran out of time is a `timeout`
        failure, with what it printed."""
        self._writable("run a command")
        if self._effect is Effect.UNSAFE and self._commands:
            raise ToolFailed(ToolFailure.PERMANENT, "an unsafe tool runs one command a call")
        undeclared = sorted(set(secrets) - set(self._secrets))
        if undeclared:
            raise ToolFailed(
                ToolFailure.PERMANENT, f"the tool does not declare the secrets {undeclared}"
            )
        uses = tuple(self._secrets[name] for name in secrets)
        for use in uses:
            await self._audit(use)
        self._commands += 1
        result = await self._transport.run(
            self._workspace,
            CommandSpec(
                argv=argv,
                cwd=cwd,
                env=env,
                secrets=uses,
                key=self._key,
                epoch=self._epoch,
                deadline=self.deadline,
                max_output=max_output,
            ),
            self._on_output,
        )
        if result.timed_out:
            raise ToolFailed(ToolFailure.TIMEOUT, command_text(result, self._answer_chars))
        return result

    async def read_file(self, path: str, max_bytes: int) -> bytes:
        return await self._transport.read_file(self._workspace, path, max_bytes)

    async def write_file(self, path: str, data: bytes) -> None:
        self._writable("write a file")
        await self._transport.write_file(self._workspace, path, data, self._epoch)

    async def list_files(self, path: str, limit: int) -> list[FileEntry]:
        return await self._transport.list_files(self._workspace, path, limit)

    def _writable(self, what: str) -> None:
        if self._read_only:
            raise ToolFailed(ToolFailure.PERMANENT, f"a preflight does not {what}")


class ToolInterface(ABC):
    """A tool. Its spec is what the model sees and what policy reads."""

    @property
    @abstractmethod
    def spec(self) -> ToolSpec: ...

    @abstractmethod
    async def target(self, ctx: TenantContext, call_input: ToolInput) -> Target:
        """What the call acts on, with the attributes policy keys on, read
        from the system the call acts on and never from what the input
        claims about it. A call with nothing in particular to act on
        answers an empty target."""
        ...

    @abstractmethod
    async def preflight(
        self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime
    ) -> None:
        """Refuses a call that cannot succeed, before anyone is asked to
        approve it, by raising `ToolFailed`. A tool with nothing to check
        returns."""
        ...

    @abstractmethod
    async def run(
        self, ctx: TenantContext, call_input: ToolInput, runtime: ToolRuntime
    ) -> Platform:
        """Does the call and returns its output, an instance of the spec's
        `output_model`; a job tool starts its work and answers `JobStarted`.
        A failure is a `ToolFailed` with its class, decided here, where it
        happens."""
        ...


class JobToolInterface(ToolInterface):
    """A tool in `job` mode. Its `run` starts work that outlives the run,
    under the call's key (`ToolRuntime.key`) and by the runtime's deadline,
    and answers `JobStarted`; starting under a key that started before
    attaches to that work, so a recovered run starts no second job. The work's completion
    arrives as an event, and the response is written from it."""

    @abstractmethod
    async def cancel(self, ctx: TenantContext, job: JobHandle) -> None:
        """Ends the work, as cancelling its loop does."""
        ...
