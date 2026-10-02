"""The windows swimlane: what one model request reads. A session grows
without limit and a model reads a bounded amount; a window is the view
between the two, sized for one model, and rendering it is deterministic so
the same steps give the same bytes.

The main model role's window is the active one, pinned to the right edge
of the history and starting at the latest summary. When it nears its
limit, a principal asks, or a provider refuses its prompt as too long, it
compacts: the summarizer folds the oldest part into a `summary` step, and
the steps themselves never change. A side model role reads a consistent
suffix sized to its own fill. A tool result too large for a step is kept
as an artifact the agent reads a page at a time."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.context import TenantContext
from acme.om.models.types.fill import MAIN, ModelRole
from acme.om.steps.types.step import Step
from acme.om.windows.types.artifact import ArtifactPage
from acme.om.windows.types.kind import KindPrompts
from acme.om.windows.types.window import RenderedRequest


class WindowsManagerInterface(ABC):
    @abstractmethod
    async def render_request(
        self,
        ctx: TenantContext,
        session_id: UUID,
        epoch: int,
        loop_id: UUID,
        kind: KindPrompts,
        role: ModelRole = MAIN,
        *,
        plan: str | None = None,
    ) -> RenderedRequest:
        """The next request of `role` over the session's history, rendered
        with the fill the session's fill set names for it; the caller records
        it (`rules.request_step`) before it calls. The main role's window
        compacts first, once, when it nears its limit (a switch to a smaller
        window included) or a `compact` control waits; the compaction's steps
        are appended under `epoch` in the loop `loop_id`, and a run that lost
        its claim is `StaleWriter`. A compaction whose summary failed is
        recorded and answers the control; the window is then read
        uncompacted while it fits its model, and the summarizer is not asked
        again near the limit before the next summary. A window that fits no
        more and cannot be compacted is `CompactionFailed`. A main request
        over a tool call still open is `PreconditionFailed`. A side role
        reads a consistent suffix sized to its own fill and never compacts.
        `plan` is the agent's current plan, which renders last."""
        ...

    @abstractmethod
    async def render_after_overflow(
        self,
        ctx: TenantContext,
        session_id: UUID,
        epoch: int,
        loop_id: UUID,
        kind: KindPrompts,
        refused: RenderedRequest,
        *,
        plan: str | None = None,
    ) -> RenderedRequest:
        """The one retry a main request gets after its provider refused it as
        too long: a compaction, then the request rendered again and marked as
        the retry. A retry refused again, a side role's request, or a window
        with nothing left to fold is `ContextOverflow`, with nothing written:
        the loop ends `errored` rather than compact again."""
        ...

    @abstractmethod
    async def bound_tool_response(self, ctx: TenantContext, session_id: UUID, step: Step) -> Step:
        """A `tool_response` as the history keeps it: as given when its
        result is within the size bound; above it, its whole text kept as an
        artifact of the session and the step holding the head, the tail, and
        the artifact's handle. Any other step is `ValidationFailed`."""
        ...

    @abstractmethod
    async def get_artifact(
        self, ctx: TenantContext, session_id: UUID, artifact_id: UUID, offset: int, limit: int
    ) -> ArtifactPage:
        """A page of an artifact: its characters from `offset`, at most
        `limit` and the policy's page. An artifact the tenant's session does
        not hold is `NotFound`."""
        ...
