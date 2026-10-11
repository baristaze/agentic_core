"""The tools swimlane: where the engine touches the world. It decides who
must agree to a call, runs it in the session's workspace through the
transport, and answers it with a response the model reads; it decides what
a new run may repeat after a crash, and records a person's decision on a
call. The loop writes the steps; this namespace builds the ones it answers
with, and writes only a person's decision and its own audit entries.

A call's request step is in the history, written and audited, before any
operation here sees it. Its input is the tool use's, and it must hash to
what the request recorded, under the key of its session.

What a call keeps of its session's content goes under that session's key
too: its input's hash is keyed by it, and the transport's record of its
command keeps the output sealed by it. So does a snapshot of its workspace:
sealed by that key, stored under a hash keyed by it, and named in the
history by a step (ADR 1027)."""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from acme.infra.transports import OutputSink
from acme.infra.workspaces import IsolationSpec, Workspace
from acme.om.context import TenantContext
from acme.om.steps.types.header import WorkspaceSnapshot
from acme.om.steps.types.step import Step
from acme.om.tools.registry import ToolRegistry
from acme.om.tools.types.call import Gate, JobHandle, JobNotStarted
from acme.om.tools.types.policy import PolicyLayer, ToolPolicy

KeyedHash = Callable[[TenantContext, UUID, bytes], Awaitable[str]]
"""A hash of a value keyed by its session: the privacy namespace's, which the
root wires."""


class ToolsManagerInterface(ABC):
    @abstractmethod
    async def get_policy(self, ctx: TenantContext) -> ToolPolicy:
        """The tenant's layer of policy; an empty one, never stored, when it
        has written none."""
        ...

    @abstractmethod
    async def write_policy(self, ctx: TenantContext, policy: ToolPolicy) -> ToolPolicy:
        """Writes the tenant's layer, as one who manages its members may:
        the first write creates it, and each later one is a compare-and-set
        on the version the caller read (`PreconditionFailed` when another
        landed first). Announced like any change."""
        ...

    @abstractmethod
    async def prepare_workspace(
        self,
        ctx: TenantContext,
        session_id: UUID,
        spec: IsolationSpec,
        restore: WorkspaceSnapshot | None = None,
    ) -> Workspace:
        """The session's workspace, prepared to its spec, before its loop's
        first model call. `IsolationRefused` when the provider cannot meet
        the spec: never a weaker workspace. A spec of no workspace answers
        the absent one, which every transport refuses, loudly.

        With `restore`, a snapshot the session's history names, the
        workspace starts from it, replaced whole. Its bytes are read from
        the store, opened under the session's key, and held to its hash
        first: one that is gone, erased with its key, altered, or of
        another hash loses the workspace (`WorkspaceLost`) before anything
        starts."""
        ...

    @abstractmethod
    async def release_workspace(self, ctx: TenantContext, workspace: Workspace) -> None:
        """Lets the workspace's instance go between loops; its files stay."""
        ...

    @abstractmethod
    async def snapshot_workspace(
        self,
        ctx: TenantContext,
        session_id: UUID,
        workspace: Workspace,
        *,
        epoch: int,
        loop_id: UUID,
    ) -> Step:
        """A snapshot of the session's live workspace, kept, and the
        `snapshotted` step that names it, appended in the loop under
        `loop_id` and under the run's `epoch`. Every credential the broker
        attached there is taken back first, and the snapshot holds no value
        of a secret the catalog's tools may be given: one that does is
        refused, and nothing is kept. It is sealed under the session's key
        and stored under its keyed hash. `SnapshotRefused` before anything
        runs when the provider cannot snapshot, the workspace holds no
        instance, or the session keeps no content at rest. When to take one
        is the caller's."""
        ...

    @abstractmethod
    async def fork_snapshot(
        self, ctx: TenantContext, parent_id: UUID, child_id: UUID, snapshot_id: UUID
    ) -> WorkspaceSnapshot:
        """A copy, for the child, of the latest snapshot its parent's history
        names: opened under the parent's key and held to its hash, sealed
        again under the child's, and stored as the child's under `snapshot_id`,
        so neither purge nor revocation of one reaches the other. It names
        the parent's workspace as the one it came from. A copy made before
        is made again the same. `ValidationFailed` when the parent holds no
        snapshot; `WorkspaceLost` when its snapshot is gone or altered."""
        ...

    @abstractmethod
    async def find_snapshot(
        self, ctx: TenantContext, session_id: UUID, snapshot_id: UUID
    ) -> WorkspaceSnapshot:
        """The snapshot under `snapshot_id` that the session's history names:
        one it took, or one a restore started it from. `NotFound` when no
        step of it names one, so a session never starts from another's."""
        ...

    @abstractmethod
    async def input_hash(
        self, ctx: TenantContext, session_id: UUID, call_input: Mapping[str, Any]
    ) -> str:
        """The hash a call's request records of its input, which an approval
        binds: keyed by the session, so equal inputs hash alike within it,
        and apart across sessions, and once its key is revoked nothing can
        confirm what the hash stood for. `KeyRevoked` when it is."""
        ...

    @abstractmethod
    async def gate(
        self,
        ctx: TenantContext,
        registry: ToolRegistry,
        defaults: PolicyLayer,
        request: Step,
        call_input: Mapping[str, Any],
        workspace: Workspace,
        *,
        holds_private: bool = True,
        tree_deadline: datetime | None = None,
        above: Sequence[PolicyLayer] = (),
    ) -> Gate:
        """Where a call stands before it runs. A tool the registry does not
        hold, an input its schema refuses, and a preflight that refuses are
        answered at once; the preflight runs by the call's own time, never
        past `tree_deadline`. Then attribution answers whose authority the
        call runs under, asked again of the adopter's transition (`denied`
        for a delegated principal who no longer holds it; `PrincipalLapsed`
        for a steady one), and the rule of two: whether the session is marked,
        `holds_private` (its private data or credentials), and whether the
        call acts outward, read from its target. Then policy decides from the
        tool, its class, its effect, and its target's attributes (`defaults`
        are the agent kind's; `above` are the kinds' above it in its tree,
        and the call is decided under each of them too, the strictest
        decision holding), and a call the rule of two holds needs a
        person even where policy allows it: an allowed call runs, under the
        context the gate's `authority` carries; a denied one is answered
        `denied`; and one that needs approval runs on a person's approval of
        exactly this call, is answered `denied` on a denial, and otherwise
        asks: the loop parks. A decision counts only while the policy lets
        the role its person decided in decide the call's class."""
        ...

    @abstractmethod
    async def execute(
        self,
        ctx: TenantContext,
        registry: ToolRegistry,
        request: Step,
        call_input: Mapping[str, Any],
        workspace: Workspace,
        *,
        epoch: int,
        tree_deadline: datetime | None,
        on_output: OutputSink | None = None,
    ) -> Step:
        """Runs a call its gate let run, under the request's id as its key and
        the run's epoch, and answers it with its response step: a result, or
        a failure with its class. Its time is the least of its tool's
        timeout, the engine's limit, and what is left before the tree's
        deadline, and its whole process tree ends then. A `transient` failure
        of a `read_only` or `idempotent` tool is run again once, after the
        options' wait, while that time allows. Each secret it uses is
        audited by name before its command runs. `StaleWriter` when the
        transport refuses the run's epoch."""
        ...

    @abstractmethod
    async def recover(
        self,
        ctx: TenantContext,
        registry: ToolRegistry,
        request: Step,
        call_input: Mapping[str, Any],
        workspace: Workspace,
        *,
        epoch: int,
        tree_deadline: datetime | None,
        on_output: OutputSink | None = None,
    ) -> Step:
        """Settles a request a lost run left without a response, after its
        gate let it run. A `read_only` or `idempotent` call runs again under
        the same key. An `unsafe` one is never repeated: its response is the
        transport's record of how its command ended, or, with none,
        `interrupted`, its outcome unknown. Asking for the record admits this
        run's epoch on the transport, so the lost run's command for the call
        is refused from then on."""
        ...

    @abstractmethod
    async def start_job(
        self,
        ctx: TenantContext,
        registry: ToolRegistry,
        request: Step,
        call_input: Mapping[str, Any],
        workspace: Workspace,
        *,
        epoch: int,
        tree_deadline: datetime | None,
    ) -> JobHandle | JobNotStarted | Step:
        """Starts a `job` call its gate let run, under the request's id, by a
        deadline of its own never later than the tree's, and answers the
        handle the loop parks on. A start refused before any work began, its
        input's refusal or the tool's `JobRefused`, answers `JobNotStarted`;
        any other failure answers the call's response, since the work may
        have started. Starting it again attaches to the work it started."""
        ...

    @abstractmethod
    async def cancel_job(self, ctx: TenantContext, registry: ToolRegistry, job: JobHandle) -> None:
        """Ends a job, as cancelling its loop does."""
        ...

    @abstractmethod
    async def decide_call(
        self,
        ctx: TenantContext,
        session_id: UUID,
        request_seq: int,
        *,
        approve: bool,
        note: str = "",
    ) -> Step:
        """A person's decision on the tool request at `request_seq`, bound to
        its tool and its input's hash, recorded as a control step in the
        session's history. An approval expires; a denial's note is what the
        model reads. Only a person whose role the tenant lets approve the
        call's class decides it (`NotAuthorized` otherwise), and the
        decision records that role; `NotFound` when no tool request is at
        that place. A decision sent on an API key is recorded as a
        program's, and no verdict counts it: an approval is a person's."""
        ...

    @abstractmethod
    async def purge_workspace(self, org_id: UUID, session_id: UUID) -> None:
        """Platform-internal: what a session the sweep has claimed for its
        purge left where its tools ran goes with its history: its workspace,
        files and instance, the transport's records of how each command
        there ended, and every snapshot kept of it. For no principal. A
        process with no workspace provider or transport holds none of it,
        and removes nothing."""
        ...

    @abstractmethod
    async def purge_tenant(self, ctx: TenantContext) -> int:
        """The sweep, for one tenant past its own retention: its policy. Any
        other tenant returns 0 and reads nothing."""
        ...
