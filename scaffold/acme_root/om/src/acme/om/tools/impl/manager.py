import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import Field, ValidationError

from acme.infra.agent_workspaces import (
    AgentWorkspace,
    AgentWorkspaceLost,
    AgentWorkspaceProviderInterface,
    Durability,
    IsolationMode,
    IsolationRefused,
    IsolationSpec,
    SnapshotRefused,
)
from acme.infra.exceptions import InfraException
from acme.infra.transports import (
    CredentialBrokerInterface,
    OutputSink,
    RecordSeal,
    SecretUse,
    TransportInterface,
)
from acme.om.agent_steps import AgentStepsManagerInterface
from acme.om.agent_steps.rules import actor_of, origin_of
from acme.om.agent_steps.types.agent_step import AgentStep, AgentStepType
from acme.om.agent_steps.types.content import UNPARSED, Content, TextBlock
from acme.om.agent_steps.types.header import (
    AgentWorkspaceSnapshot,
    ControlCommand,
    ControlHeader,
    DecidedCall,
    ToolFailure,
    ToolRequestHeader,
)
from acme.om.attribution import AttributionManagerInterface
from acme.om.attribution.types.authority import CallReach
from acme.om.base import OmBase, derived_id, new_id, thaw_mapping, utcnow
from acme.om.change_events import ChangeEventsManagerInterface
from acme.om.change_events.manager import audit_change_event
from acme.om.context import Permission, TenantContext
from acme.om.exceptions import (
    AuthorityRevoked,
    JobRefused,
    NotAuthorized,
    NotFound,
    OmException,
    PreconditionFailed,
    StaleWriter,
    ToolFailed,
    UniqueKeyTaken,
    ValidationFailed,
)
from acme.om.outbox import OutboxRelayInterface
from acme.om.outbox.types.row import OutboxRow, versioned_row
from acme.om.tenancy import TenancyManagerInterface
from acme.om.tools.impl.bases import AgentWorkspaceBases
from acme.om.tools.impl.snapshots import SnapshotStore
from acme.om.tools.manager import KeyedHash, ToolsManagerInterface
from acme.om.tools.registry import ToolRegistry
from acme.om.tools.rules import (
    CAPABILITY_MISSING,
    DEFAULT_CEILINGS,
    INPUT_HASH,
    STALE_STATUS,
    approver_roles,
    bounded_json,
    call_deadline,
    call_refusal,
    canonical_input,
    command_text,
    decide,
    engine_retries,
    infra_failure,
    injected_secrets,
    job_deadline,
    named_snapshot,
    reaches_outward,
    recovered_text,
    response,
    snapshotted_step,
    strictest,
    verdict,
    with_reach,
)
from acme.om.tools.seal import RecordSealInterface
from acme.om.tools.storage import ToolStorageInterface
from acme.om.tools.tool import JobToolInterface, TakenSnapshot, ToolInterface, ToolRuntime
from acme.om.tools.types.call import (
    Gate,
    GateOutcome,
    JobHandle,
    JobNotStarted,
    JobStarted,
    Verdict,
)
from acme.om.tools.types.policy import Decision, ToolPolicy, ToolPolicyCall, ToolPolicyLayer
from acme.om.tools.types.tool import ToolInput, ToolMode

log = logging.getLogger(__name__)

CREATED = "tools.tool_policy.created"
UPDATED = "tools.tool_policy.updated"
SECRET_USED = "tools.secret.used"


class ToolsOptions(OmBase):
    # The platform's: no tenant loosens a call past them.
    ceilings: ToolPolicyLayer = Field(default_factory=lambda: DEFAULT_CEILINGS)
    engine_limit: timedelta = timedelta(minutes=30)  # the engine's ceiling on one call
    approval_lifetime: timedelta = timedelta(hours=1)  # an approval expires, and is asked again
    max_output_chars: int = 50_000  # what the model reads of one output, at most
    history_page: int = 200  # steps one read of a call's later history takes
    purge_batch: int = 1000  # policies one purge statement deletes at most
    # How long past a call's deadline a tool that is not ending is waited on
    # before it is cut: the transport ends a command's tree at the deadline
    # itself, and returns what the command printed.
    grace: timedelta = timedelta(seconds=5)
    # How long the engine waits before it runs again, once, a call of a tool
    # a repeat cannot harm that failed in a way that may pass on its own.
    retry_wait: timedelta = timedelta(seconds=2)
    # The most a workspace base's build takes, its setup's commands together;
    # its claim holds as long, so a build lost with its process is made again.
    base_build_limit: timedelta = timedelta(minutes=30)


class ToolsManagerImpl(ToolsManagerInterface):
    def __init__(
        self,
        storage: ToolStorageInterface,
        steps: AgentStepsManagerInterface,
        tenancy: TenancyManagerInterface,
        change_events: ChangeEventsManagerInterface,
        relay: OutboxRelayInterface,
        workspaces: AgentWorkspaceProviderInterface,
        transport: TransportInterface,
        options: ToolsOptions,
        clock: Callable[[], datetime] = utcnow,
        *,
        keyed_hash: KeyedHash,
        record_seal: RecordSealInterface,
        attribution: AttributionManagerInterface,
        broker: CredentialBrokerInterface,
        snapshots: SnapshotStore,
        bases: AgentWorkspaceBases,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._broker = broker
        self._snapshots = snapshots
        self._bases = bases
        self._keyed_hash = keyed_hash
        self._record_seal = record_seal
        self._sleep = sleep
        self._attribution = attribution
        self._storage = storage
        self._steps = steps
        self._tenancy = tenancy
        self._change_events = change_events
        self._relay = relay
        self._agent_workspaces = workspaces
        self._transport = transport
        self._options = options
        self._clock = clock

    # The tenant's layer.

    async def get_policy(self, ctx: TenantContext) -> ToolPolicy:
        ctx.require(Permission.READ)
        return await self._policy(ctx)

    async def write_policy(self, ctx: TenantContext, policy: ToolPolicy) -> ToolPolicy:
        ctx.require(Permission.MANAGE_MEMBERS)
        stored = await self._storage.read_policy(ctx.org_id)
        now = self._clock()
        if stored is None:
            created = ToolPolicy.model_validate(
                {
                    **policy.model_dump(),
                    "created_at": now,
                    "updated_at": now,
                    "created_by": ctx.user_id,
                    "updated_by": ctx.user_id,
                    "version": 1,
                }
            )
            rows = (versioned_row(ctx, CREATED, created.id, created.version),)
            try:
                landed = await self._storage.create_policy(ctx.org_id, created, rows)
            except UniqueKeyTaken as error:
                raise PreconditionFailed(
                    "the tenant's tool policy was written meanwhile"
                ) from error
            if not landed:
                raise PreconditionFailed(f"tool policy {created.id} is written already")
            await self._relay_all(ctx, rows)
            return created
        if policy.version != stored.version:
            raise PreconditionFailed(f"tool policy {stored.id} is at version {stored.version}")
        # The copy starts from the stored row: its id and provenance stay.
        updated = stored.model_copy(
            update={
                "rules": policy.rules,
                "approvers": policy.approvers,
                "version": stored.version + 1,
                "updated_at": now,
                "updated_by": ctx.user_id,
            }
        )
        rows = (versioned_row(ctx, UPDATED, updated.id, updated.version),)
        await self._storage.write_policy(ctx.org_id, updated, stored.version, rows)
        await self._relay_all(ctx, rows)
        return updated

    # The workspace.

    async def prepare_agent_workspace(
        self,
        ctx: TenantContext,
        agent_session_id: UUID,
        spec: IsolationSpec,
        restore: AgentWorkspaceSnapshot | None = None,
    ) -> AgentWorkspace:
        ctx.require(Permission.WRITE)
        if spec.mode is IsolationMode.NONE:
            if restore is not None:
                raise IsolationRefused(
                    f"agent session {agent_session_id} has no workspace to start from a snapshot",
                    cause="a session with no workspace starts from no snapshot",
                )
            return AgentWorkspace.absent(ctx.org_id, agent_session_id)
        # Its bytes are trusted by their hash before the provider sees them:
        # a snapshot that does not match starts nothing.
        archive = (
            None if restore is None else await self._snapshots.load(ctx, agent_session_id, restore)
        )
        # A snapshot holds its base already; without one, a base with setup
        # is read, or built once for the tenant.
        base = None
        if archive is None and spec.base is not None and spec.base.setup:
            base = await self._bases.archive(ctx, spec)
        try:
            return await self._agent_workspaces.prepare(
                ctx.org_id, agent_session_id, spec, snapshot=archive, base=base
            )
        except InfraException as lost:
            if base is None or lost.code != AgentWorkspaceLost.code:
                raise
            # A base this host cannot bring in, such as one on an image this
            # host cannot pull, holds nothing of the session's: it is built
            # again, and the loop asks again once it is.
            await self._bases.drop(ctx, spec)
            raise IsolationRefused(
                f"the workspace base is built again: {lost.message}",
                clears=True,
                cause="the workspace base is built again",
            ) from lost

    async def release_agent_workspace(self, ctx: TenantContext, workspace: AgentWorkspace) -> None:
        ctx.require(Permission.WRITE)
        if workspace.spec.mode is not IsolationMode.NONE:
            await self._agent_workspaces.release(workspace)

    async def snapshot_agent_workspace(
        self,
        ctx: TenantContext,
        agent_session_id: UUID,
        workspace: AgentWorkspace,
        *,
        epoch: int,
        loop_id: UUID,
        tools: Sequence[ToolInterface],
    ) -> AgentStep:
        ctx.require(Permission.WRITE)
        archive = await self._take(ctx, agent_session_id, workspace, tools)
        return await self._name(
            ctx, agent_session_id, workspace, archive, epoch=epoch, loop_id=loop_id
        )

    async def fork_snapshot(
        self, ctx: TenantContext, child_id: UUID, snapshot_id: UUID, taken: TakenSnapshot
    ) -> AgentWorkspaceSnapshot:
        ctx.require(Permission.WRITE)
        # The child's own copy: what the provider keeps outside the archive,
        # such as a disk, is copied under the child's workspace, so neither
        # session's purge nor revocation reaches the other's.
        copy = await self._agent_workspaces.keep(taken.archive, ctx.org_id, child_id)
        try:
            kept = await self._snapshots.keep(
                ctx, child_id, snapshot_id, taken.agent_workspace_id, copy
            )
        except BaseException:
            await self._agent_workspaces.discard(copy)
            raise
        if not taken.kept:
            # A cache keeps nothing of what the spawn took: the child's copy
            # holds it all.
            await self._agent_workspaces.discard(taken.archive)
        return kept

    async def find_snapshot(
        self, ctx: TenantContext, agent_session_id: UUID, snapshot_id: UUID
    ) -> AgentWorkspaceSnapshot:
        ctx.require(Permission.READ)
        async for step in self._history(ctx, agent_session_id):
            named = named_snapshot(step)
            if named is not None and named.id == snapshot_id:
                return named
        raise NotFound(f"agent session {agent_session_id} names no snapshot {snapshot_id}")

    async def _take(
        self,
        ctx: TenantContext,
        agent_session_id: UUID,
        workspace: AgentWorkspace,
        tools: Sequence[ToolInterface],
    ) -> bytes:
        """The live workspace's archive, refused before anything runs when
        there is none to take or the session keeps no content at rest. No
        credential is in it: the broker takes back all it attached there,
        one a lost run never took back included, and an injected secret's
        value a command wrote there refuses it, one a tool of `tools`, the
        session's beyond the catalog, injected included."""
        if workspace.spec.mode is IsolationMode.NONE:
            raise SnapshotRefused(f"agent session {agent_session_id} has no workspace to snapshot")
        await self._snapshots.at_rest(ctx, agent_session_id)
        await self._broker.detach_all(workspace)
        archive = await self._agent_workspaces.snapshot(workspace)
        try:
            await self._snapshots.scan(
                ctx,
                self._agent_workspaces.held(archive),
                also=injected_secrets(tool.spec for tool in tools),
            )
        except BaseException:
            # Refused, so nothing of it stays: a disk its provider keeps
            # apart, one that holds a secret's value included, goes too.
            await self._agent_workspaces.discard(archive)
            raise
        return archive

    async def _name(
        self,
        ctx: TenantContext,
        agent_session_id: UUID,
        workspace: AgentWorkspace,
        archive: bytes,
        *,
        epoch: int,
        loop_id: UUID,
    ) -> AgentStep:
        """`archive` kept as the session's snapshot, and the `snapshotted`
        step that names it, appended under the run's epoch."""
        try:
            kept = await self._snapshots.keep(
                ctx, agent_session_id, new_id(), workspace.id, archive
            )
        except BaseException:
            # Not kept, so nothing of it stays, a disk kept apart included.
            await self._agent_workspaces.discard(archive)
            raise
        step = snapshotted_step(new_id(), self._clock(), agent_session_id, loop_id, kept)
        (stored,) = await self._steps.append_agent_steps(ctx, agent_session_id, epoch, [step])
        return stored

    async def _fork_point(
        self,
        ctx: TenantContext,
        registry: ToolRegistry,
        request: AgentStep,
        workspace: AgentWorkspace,
        epoch: int,
    ) -> TakenSnapshot:
        """The call's workspace as it stands, taken for a sub-agent to start
        from, scanned for what each tool of `registry` may have injected. A
        workspace kept by snapshots keeps it as its own too, named in the
        call's loop, so its history holds the state the child started from;
        a cache keeps nothing, and the child alone keeps it."""
        agent_session_id = request.agent_session_id
        archive = await self._take(ctx, agent_session_id, workspace, registry.tools())
        kept = workspace.spec.durability is Durability.SNAPSHOT
        if kept:
            await self._name(
                ctx, agent_session_id, workspace, archive, epoch=epoch, loop_id=request.loop_id
            )
        return TakenSnapshot(agent_workspace_id=workspace.id, archive=archive, kept=kept)

    # A call.

    async def input_hash(
        self, ctx: TenantContext, agent_session_id: UUID, call_input: Mapping[str, Any]
    ) -> str:
        ctx.require(Permission.WRITE)
        return INPUT_HASH + await self._keyed_hash(
            ctx, agent_session_id, canonical_input(call_input)
        )

    async def gate(
        self,
        ctx: TenantContext,
        registry: ToolRegistry,
        defaults: ToolPolicyLayer,
        request: AgentStep,
        call_input: Mapping[str, Any],
        workspace: AgentWorkspace,
        *,
        holds_private: bool = True,
        tree_deadline: datetime | None = None,
        above: Sequence[ToolPolicyLayer] = (),
    ) -> Gate:
        ctx.require(Permission.WRITE)
        resolved = await self._resolve(ctx, registry, request, call_input)
        if isinstance(resolved, AgentStep):
            return Gate(outcome=GateOutcome.REFUSE, response=resolved)
        tool, parsed = resolved
        if _header(request).authorization_class != tool.spec.authorization_class:
            raise ValidationFailed(
                f"request {request.id} names the class {_header(request).authorization_class}, "
                f"and {tool.spec.name} is {tool.spec.authorization_class}"
            )
        now = self._clock()
        deadline = call_deadline(now, tool.spec.timeout, self._options.engine_limit, tree_deadline)
        runtime = self._runtime(
            ctx, registry, request, tool, workspace, 0, deadline, None, read_only=True
        )
        try:
            async with asyncio.timeout(self._seconds_until(deadline)):
                await tool.preflight(ctx, parsed, runtime)
                target = await tool.target(ctx, parsed)
        except Exception as error:
            failure, detail = self._classify(error, tool)
            return Gate(outcome=GateOutcome.REFUSE, response=self._answer(request, detail, failure))
        asked = ToolPolicyCall(
            tool=tool.spec.name,
            authorization_class=tool.spec.authorization_class,
            effect=tool.spec.effect,
            target=target,
        )
        call = with_reach(asked)
        # Whose authority the call runs under, asked of the adopter's
        # transition on every call, and the rule of two: a marked session
        # holding private data that acts outward waits for a person, however
        # policy would decide. A delegated principal who no longer holds the
        # call is answered `denied`; a steady one that lapsed raises, and the
        # loop waits for a person to take the session over.
        reach = CallReach(
            outward=reaches_outward(asked, workspace.spec.egress.mode), holds_private=holds_private
        )
        try:
            authority = await self._attribution.authorize_call(ctx, request.agent_session_id, reach)
        except AuthorityRevoked as revoked:
            denied = self._answer(request, revoked.message, ToolFailure.DENIED)
            return Gate(outcome=GateOutcome.REFUSE, response=denied)
        # The class's permission, asked of that live context: a principal
        # who still holds a place but lost what the call needs is denied.
        refusal = call_refusal(authority.context, call.authorization_class)
        if refusal is not None:
            denied = self._answer(request, refusal, ToolFailure.DENIED)
            return Gate(outcome=GateOutcome.REFUSE, response=denied)
        policy = await self._policy(ctx)
        # A sub-agent's call is decided under its own kind's defaults and
        # under those of every kind above it: a kind looser than its parent's
        # never runs a call the parent's would hold.
        decision = strictest(
            *(
                decide(call, layer, policy.layer(), self._options.ceilings)
                for layer in (defaults, *above)
            )
        )
        if decision is Decision.ALLOW and authority.needs_person:
            decision = Decision.APPROVE
        if decision is Decision.ALLOW:
            return Gate(outcome=GateOutcome.RUN, decision=decision, authority=authority)
        if decision is Decision.DENY:
            detail = f"policy does not allow {tool.spec.name}, a {call.authorization_class} call"
            denied = self._answer(request, detail, ToolFailure.DENIED)
            return Gate(outcome=GateOutcome.REFUSE, decision=decision, response=denied)
        approvers = approver_roles(policy, call.authorization_class)
        later = await self._after(ctx, request)
        found, decision_step = verdict(request, later, self._clock(), approvers)
        if found is Verdict.APPROVED:
            return Gate(outcome=GateOutcome.RUN, decision=decision, authority=authority)
        if found is Verdict.DENIED and decision_step is not None:
            note = decision_step.as_text() or "no reason was given"
            denied = self._answer(request, f"a person denied this call: {note}", ToolFailure.DENIED)
            return Gate(outcome=GateOutcome.REFUSE, decision=decision, response=denied)
        return Gate(outcome=GateOutcome.ASK, decision=decision, authority=authority)

    async def execute(
        self,
        ctx: TenantContext,
        registry: ToolRegistry,
        request: AgentStep,
        call_input: Mapping[str, Any],
        workspace: AgentWorkspace,
        *,
        epoch: int,
        tree_deadline: datetime | None,
        on_output: OutputSink | None = None,
    ) -> AgentStep:
        ctx.require(Permission.WRITE)
        resolved = await self._resolve(ctx, registry, request, call_input)
        if isinstance(resolved, AgentStep):
            return resolved
        tool, parsed = resolved
        if tool.spec.mode is ToolMode.JOB:
            raise ValidationFailed(f"{tool.spec.name} is a job: it is started, not executed")
        deadline = call_deadline(
            self._clock(), tool.spec.timeout, self._options.engine_limit, tree_deadline
        )
        retried = False
        while True:
            runtime = self._runtime(
                ctx, registry, request, tool, workspace, epoch, deadline, on_output
            )
            try:
                async with asyncio.timeout(self._seconds_until(deadline + self._options.grace)):
                    output = await tool.run(ctx, parsed, runtime)
            except Exception as error:
                failure, detail = self._classify(error, tool)
                # A failure that may pass on its own, of a tool a repeat cannot
                # harm, is run again once, after a short wait, while the
                # call's time allows it; the model reads that it was.
                wait = self._options.retry_wait
                if (
                    not retried
                    and engine_retries(failure, tool.spec.effect)
                    and self._clock() + wait < deadline
                ):
                    retried = True
                    await self._sleep(wait.total_seconds())
                    continue
                if retried:
                    detail = f"{detail} (the engine ran it again once, and it failed again)"
                return self._answer(request, detail, failure)
            break
        if not isinstance(output, tool.spec.output_model):
            detail = f"{tool.spec.name} answered {type(output).__name__}, not its output type"
            return self._answer(request, detail, ToolFailure.PERMANENT)
        text = output.model_dump_json()
        limit = self._options.max_output_chars
        if len(text) > limit:
            # Each of its strings keeps its own end, never only the last's.
            text = bounded_json(output.model_dump(mode="json"), limit)
        return self._answer(request, text)

    async def recover(
        self,
        ctx: TenantContext,
        registry: ToolRegistry,
        request: AgentStep,
        call_input: Mapping[str, Any],
        workspace: AgentWorkspace,
        *,
        epoch: int,
        tree_deadline: datetime | None,
        on_output: OutputSink | None = None,
    ) -> AgentStep:
        ctx.require(Permission.WRITE)
        tool = registry.get(_header(request).tool)
        if tool is not None and tool.spec.effect.repeatable:
            return await self.execute(
                ctx,
                registry,
                request,
                call_input,
                workspace,
                epoch=epoch,
                tree_deadline=tree_deadline,
                on_output=on_output,
            )
        # Asking admits this run's epoch on the transport first, so the lost
        # run's command for this call can no longer start there.
        try:
            recorded = await self._transport.outcome(
                workspace, request.id, epoch, seal=self._sealing(ctx, request)
            )
        except InfraException as error:
            if error.http_status == STALE_STATUS:
                raise StaleWriter(error.message) from error
            # A workspace with no transport has no record to read.
            if error.code != CAPABILITY_MISSING:
                raise
            recorded = None
        if recorded is None:
            detail = "the run that made this call was lost before the call answered"
            return self._answer(request, detail, ToolFailure.INTERRUPTED)
        limit = self._options.max_output_chars
        if recorded.timed_out:
            return self._answer(request, command_text(recorded, limit), ToolFailure.TIMEOUT)
        return self._answer(request, recovered_text(recorded, limit))

    async def start_job(
        self,
        ctx: TenantContext,
        registry: ToolRegistry,
        request: AgentStep,
        call_input: Mapping[str, Any],
        workspace: AgentWorkspace,
        *,
        epoch: int,
        tree_deadline: datetime | None,
    ) -> JobHandle | JobNotStarted | AgentStep:
        ctx.require(Permission.WRITE)
        resolved = await self._resolve(ctx, registry, request, call_input)
        if isinstance(resolved, AgentStep):
            return JobNotStarted(response=resolved)  # refused before its tool ran
        tool, parsed = resolved
        if not isinstance(tool, JobToolInterface):
            raise ValidationFailed(f"{tool.spec.name} is not a job")
        deadline = job_deadline(self._clock(), tool.spec.timeout, tree_deadline)
        runtime = self._runtime(ctx, registry, request, tool, workspace, epoch, deadline, None)
        try:
            # Starting is quick, and never outlasts the engine's limit or the
            # job's deadline; the work runs by that deadline.
            start_by = min(deadline, self._clock() + self._options.engine_limit)
            async with asyncio.timeout(self._seconds_until(start_by)):
                started = await tool.run(ctx, parsed, runtime)
        except JobRefused as refused:
            return JobNotStarted(response=self._answer(request, refused.message, refused.failure))
        except Exception as error:
            # Its tool ran: whatever it raised, the work may have started.
            failure, detail = self._classify(error, tool)
            return self._answer(request, detail, failure)
        if not isinstance(started, JobStarted):
            detail = f"{tool.spec.name} answered {type(started).__name__}, not JobStarted"
            return self._answer(request, detail, ToolFailure.PERMANENT)
        return JobHandle(
            tool=tool.spec.name,
            key=request.id,
            handle=started.handle,
            deadline=deadline,
            request_id=started.request_id,
        )

    async def cancel_job(self, ctx: TenantContext, registry: ToolRegistry, job: JobHandle) -> None:
        ctx.require(Permission.WRITE)
        tool = registry.get(job.tool)
        if not isinstance(tool, JobToolInterface):
            raise ValidationFailed(f"no job tool named {job.tool}")
        await tool.cancel(ctx, job)

    # A person's decision.

    async def decide_call(
        self,
        ctx: TenantContext,
        agent_session_id: UUID,
        request_seq: int,
        *,
        approve: bool,
        note: str = "",
    ) -> AgentStep:
        ctx.require(Permission.WRITE)
        page = await self._steps.get_agent_steps(ctx, agent_session_id, request_seq - 1, 1)
        request = next((step for step in page.items if step.seq == request_seq), None)
        if request is None or request.type is not AgentStepType.TOOL_REQUEST:
            raise NotFound(f"no tool request at {request_seq} in agent session {agent_session_id}")
        header = _header(request)
        roles = approver_roles(await self._policy(ctx), header.authorization_class)
        if ctx.role not in roles:
            raise NotAuthorized(
                f"{ctx.role.value} does not decide {header.authorization_class} calls"
            )
        now = self._clock()
        decision = AgentStep(
            id=new_id(),
            created_at=now,
            agent_session_id=agent_session_id,
            loop_id=request.loop_id,
            type=AgentStepType.CONTROL,
            # A decision sent on an API key is a program's: it is recorded as
            # one, and `decides` never counts it as a person's approval.
            actor=actor_of(ctx.credential_kind),
            origin=origin_of(ctx.app.type),
            refs=(request.id,),
            header=ControlHeader(
                command=ControlCommand.APPROVE if approve else ControlCommand.DENY,
                call=DecidedCall(
                    tool=header.tool,
                    input_hash=header.input_hash,
                    decided_by=ctx.user_id,
                    role=ctx.role,
                    expires_at=now + self._options.approval_lifetime if approve else None,
                ),
            ),
            content=Content(blocks=(TextBlock(text=note),) if note else ()),
        )
        (stored,) = await self._steps.append_inputs(ctx, agent_session_id, [decision])
        return stored

    async def purge_agent_workspace(self, org_id: UUID, agent_session_id: UUID) -> None:
        # A session's workspace is prepared under the session's id. Its files
        # go first: what still runs there ends with them. Its snapshots are
        # content too, and go with them.
        await self._agent_workspaces.purge(org_id, agent_session_id)
        await self._transport.purge_records(agent_session_id)
        await self._snapshots.purge(org_id, agent_session_id)

    async def erase_snapshots(self, ctx: TenantContext, agent_session_id: UUID) -> None:
        ctx.require(Permission.WRITE)
        # A session's workspace is prepared under the session's id, and its
        # snapshots are kept under it.
        await self._agent_workspaces.erase_snapshots(ctx.org_id, agent_session_id)

    async def purge_tenant(self, ctx: TenantContext) -> int:
        ctx.require(Permission.WRITE)
        if not await self._tenancy.tenant_expired(ctx):
            return 0
        # The tenant's workspace bases are its own, and go with it.
        bases = await self._bases.purge(ctx.org_id)
        return bases + await self._storage.purge_tenant(ctx.org_id, self._options.purge_batch)

    # Helpers.

    async def _policy(self, ctx: TenantContext) -> ToolPolicy:
        stored = await self._storage.read_policy(ctx.org_id)
        if stored is not None:
            return stored
        now = self._clock()
        return ToolPolicy(
            id=derived_id(ctx.org_id, now),
            created_at=now,
            updated_at=now,
            created_by=ctx.user_id,
            updated_by=ctx.user_id,
        )

    async def _history(
        self, ctx: TenantContext, agent_session_id: UUID
    ) -> AsyncIterator[AgentStep]:
        """The session's whole history, in `seq` order, a page at a time."""
        after = 0
        while True:
            page = await self._steps.get_agent_steps(
                ctx, agent_session_id, after, self._options.history_page
            )
            for step in page.items:
                yield step
            if not page.has_more or not page.items:
                return
            after = page.items[-1].seq

    async def _after(self, ctx: TenantContext, request: AgentStep) -> list[AgentStep]:
        """The session's history after the request, every page of it."""
        later: list[AgentStep] = []
        after = request.seq
        while True:
            page = await self._steps.get_agent_steps(
                ctx, request.agent_session_id, after, self._options.history_page
            )
            later += page.items
            if not page.has_more or not page.items:
                return later
            after = page.items[-1].seq

    async def _resolve(
        self,
        ctx: TenantContext,
        registry: ToolRegistry,
        request: AgentStep,
        call_input: Mapping[str, Any],
    ) -> tuple[ToolInterface, ToolInput] | AgentStep:
        """The tool and the typed input, or the response that refuses them: a
        name the registry does not hold, or an input its schema refuses. An
        input that is not the one the request recorded is never run."""
        header = _header(request)
        if await self.input_hash(ctx, request.agent_session_id, call_input) != header.input_hash:
            raise ValidationFailed(f"the input given is not the one request {request.id} recorded")
        tool = registry.get(header.tool)
        if tool is None:
            names = ", ".join(t.spec.name for t in registry.tools()) or "none"
            detail = f"there is no tool named {header.tool!r}; the tools are: {names}"
            return self._answer(request, detail, ToolFailure.INVALID_INPUT)
        if UNPARSED in call_input:
            # What the model wrote for the input is not a JSON object.
            detail = (
                "the input is not one JSON object, so the call never ran; write the "
                "whole input as one JSON object that fits the tool's schema"
            )
            return self._answer(request, detail, ToolFailure.INVALID_INPUT)
        try:
            return tool, tool.spec.input_model.model_validate(thaw_mapping(call_input))
        except ValidationError as error:
            detail = "; ".join(
                f"{'.'.join(str(part) for part in item['loc']) or 'input'}: {item['msg']}"
                for item in error.errors()
            )
            return self._answer(request, detail, ToolFailure.INVALID_INPUT)

    def _runtime(
        self,
        ctx: TenantContext,
        registry: ToolRegistry,
        request: AgentStep,
        tool: ToolInterface,
        workspace: AgentWorkspace,
        epoch: int,
        deadline: datetime,
        on_output: OutputSink | None,
        *,
        read_only: bool = False,
    ) -> ToolRuntime:
        async def audit(use: SecretUse) -> None:
            entry = audit_change_event(
                ctx,
                derived_id(request.id, request.created_at, f"secret:{use.name}"),
                SECRET_USED,
                request.id,
                {
                    "secret": use.name,
                    "via": use.via.value,
                    "tool": tool.spec.name,
                    "agent_session_id": str(request.agent_session_id),
                },
            )
            await self._change_events.append_change_event(ctx, entry)

        return ToolRuntime(
            self._transport,
            workspace,
            agent_session_id=request.agent_session_id,
            seal=self._sealing(ctx, request),
            key=request.id,
            epoch=epoch,
            deadline=deadline,
            effect=tool.spec.effect,
            secrets=tool.spec.secrets,
            audit=audit,
            snapshot=lambda: self._fork_point(ctx, registry, request, workspace, epoch),
            on_output=on_output,
            read_only=read_only,
            answer_chars=self._options.max_output_chars,
        )

    def _sealing(self, ctx: TenantContext, request: AgentStep) -> RecordSeal:
        """The seal the call's command goes to the transport with: its
        session's, bound to the call's key."""
        agent_session_id, key = request.agent_session_id, request.id
        return RecordSeal(
            seal=lambda data: self._record_seal.seal(ctx, agent_session_id, key, data),
            open=lambda sealed: self._record_seal.open(ctx, agent_session_id, key, sealed),
        )

    def _classify(self, error: Exception, tool: ToolInterface) -> tuple[ToolFailure, str]:
        """The class of a failure the tool did not class itself. An infra
        error is read by its status and its code, as a boundary translates
        one; the transport's refusal of the run's epoch ends the run
        instead (`StaleWriter`)."""
        match error:
            case ToolFailed():
                return error.failure, error.message
            case TimeoutError():
                return ToolFailure.TIMEOUT, f"{tool.spec.name} ran out of time"
            case InfraException() if error.http_status == STALE_STATUS:
                raise StaleWriter(error.message) from error
            case InfraException():
                return infra_failure(error.http_status, error.code), error.message
            case OmException():
                return ToolFailure.PERMANENT, error.message
            case _:
                log.exception("tool %s failed", tool.spec.name, exc_info=error)
                return ToolFailure.PERMANENT, f"{tool.spec.name} failed: {type(error).__name__}"

    def _answer(
        self, request: AgentStep, text: str, failure: ToolFailure | None = None
    ) -> AgentStep:
        limit = self._options.max_output_chars
        return response(new_id(), self._clock(), request, text, failure, limit=limit)

    def _seconds_until(self, moment: datetime) -> float:
        return max((moment - self._clock()).total_seconds(), 0.0)

    async def _relay_all(self, ctx: TenantContext, rows: tuple[OutboxRow, ...]) -> None:
        """The write has committed; a relay that fails is left to the sweep."""
        if rows:
            await self._relay.relay_all(ctx.org_id, rows)


def _header(request: AgentStep) -> ToolRequestHeader:
    header = request.header
    if not isinstance(header, ToolRequestHeader):
        raise ValidationFailed(f"step {request.id} is not a tool request")
    return header
