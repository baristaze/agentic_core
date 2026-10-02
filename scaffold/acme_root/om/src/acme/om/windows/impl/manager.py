from collections.abc import Callable, Sequence
from uuid import UUID

from pydantic import Field

from acme.infra.buckets import Buckets, BucketsInterface
from acme.integrations.model_providers import ModelProvidersInterface, reply_of
from acme.integrations.model_providers.calls import ModelReply
from acme.integrations.model_providers.failures import ModelCallFailed
from acme.integrations.model_providers.types import StopReason
from acme.om.base import Platform, derived_id, new_id, utcnow
from acme.om.context import Permission, TenantContext
from acme.om.exceptions import (
    CompactionFailed,
    ContextOverflow,
    NotFound,
    PreconditionFailed,
    ValidationFailed,
)
from acme.om.models.manager import ModelsManagerInterface
from acme.om.models.types.fill import MAIN, SUMMARIZER, Fill, FillSet, ModelRole
from acme.om.steps import StepsManagerInterface
from acme.om.steps.types.content import Children, Content, ToolResultBlock
from acme.om.steps.types.header import (
    ArtifactRef,
    ModelResponseHeader,
    SummaryHeader,
    ToolResponseHeader,
)
from acme.om.steps.types.step import Actor, Origin, Step, StepType
from acme.om.windows import rules
from acme.om.windows.gate import CallGateInterface
from acme.om.windows.hashes import PromptHashInterface
from acme.om.windows.manager import WindowsManagerInterface
from acme.om.windows.storage import WindowStorageInterface
from acme.om.windows.types.artifact import Artifact, ArtifactPage
from acme.om.windows.types.kind import KindPrompts
from acme.om.windows.types.policy import CompactionPolicy
from acme.om.windows.types.window import ContextWindow, RenderedRequest

BUCKET = Buckets.ARTIFACTS
CONTENT_TYPE = "text/plain; charset=utf-8"


class WindowsOptions(Platform):
    page: int = Field(default=200, gt=0)  # steps one read of the history asks for


class WindowsManagerImpl(WindowsManagerInterface):
    def __init__(
        self,
        storage: WindowStorageInterface,
        steps: StepsManagerInterface,
        models: ModelsManagerInterface,
        providers: ModelProvidersInterface,
        buckets: BucketsInterface,
        gate: CallGateInterface,
        hashes: PromptHashInterface,
        policy: CompactionPolicy,
        options: WindowsOptions,
    ) -> None:
        self._storage = storage
        self._steps = steps
        self._models = models
        self._providers = providers
        self._buckets = buckets
        self._gate = gate
        self._hashes = hashes
        self._policy = policy
        self._options = options

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
        ctx.require(Permission.WRITE)
        fill_set = await self._models.get_fill_set(ctx, session_id)
        fill = _fill(fill_set, role)
        steps = await self._history(ctx, session_id)
        if role != MAIN:
            draft = _drafted(
                lambda: rules.render_side(steps, kind, role, fill, fill_set.version, self._policy)
            )
            return await self._hashed(ctx, session_id, draft)
        draft = self._main(steps, kind, fill, fill_set.version, plan)
        fits = rules.fits(draft.window, fill)
        # Near its limit, a window compacts once, unless an attempt since the
        # latest summary failed and the window still fits: then it is read
        # uncompacted, and no summarizer is asked again until it no longer
        # fits or a principal asks. Each render compacts at most once, and a
        # compaction folds at least one exchange, so no render loops.
        near = rules.needs_compaction(draft.window, fill, self._policy)
        retried = rules.compaction_failed(steps) and fits
        if (near and not retried) or rules.compact_requested(steps):
            try:
                compacted = await self._compact(ctx, session_id, epoch, loop_id, steps, fill_set)
            except CompactionFailed:
                # The attempt is recorded, and answers the control. A window
                # that still fits is read as it is; one that does not ends here.
                if not fits:
                    raise
                compacted = None
            if compacted is not None:
                draft = self._main(compacted, kind, fill, fill_set.version, plan)
        return await self._hashed(ctx, session_id, draft)

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
        ctx.require(Permission.WRITE)
        if refused.overflow_retry:
            raise ContextOverflow("the provider refused the request's one retry as too long")
        if refused.window.role != MAIN:
            raise ContextOverflow(f"a {refused.window.role} request reads a suffix; none compacts")
        fill_set = await self._models.get_fill_set(ctx, session_id)
        fill = _fill(fill_set, MAIN)
        steps = await self._history(ctx, session_id)
        self._main(steps, kind, fill, fill_set.version, plan)
        compacted = await self._compact(ctx, session_id, epoch, loop_id, steps, fill_set)
        if compacted is None:
            raise ContextOverflow("the window holds nothing more to fold")
        draft = self._main(compacted, kind, fill, fill_set.version, plan)
        rendered = await self._hashed(ctx, session_id, draft)
        return rendered.model_copy(update={"overflow_retry": True})

    async def bound_tool_response(self, ctx: TenantContext, session_id: UUID, step: Step) -> Step:
        ctx.require(Permission.WRITE)
        header = step.header
        if not isinstance(header, ToolResponseHeader) or step.session_id != session_id:
            raise ValidationFailed(f"a {step.type.value} step is no tool response of {session_id}")
        if header.artifact is not None:
            return step
        result = step.as_tool_response()
        kept = rules.preview(result, self._policy)
        if kept is None:
            return step
        whole, parts = kept
        # Derived from the step, so a run that keeps the same response twice
        # writes the same artifact once.
        artifact_id = derived_id(step.id, step.created_at, "artifact")
        await self._buckets.put(
            ctx.org_id,
            BUCKET,
            rules.artifact_key(session_id, artifact_id),
            whole.encode("utf-8"),
            CONTENT_TYPE,
            deadline=ctx.deadline,
        )
        # The text first, then its record: a record always has its text.
        artifact = Artifact(
            id=artifact_id,
            created_at=step.created_at,
            session_id=session_id,
            step_id=step.id,
            characters=len(whole),
        )
        await self._storage.write_artifact(ctx.org_id, artifact)
        handle = ArtifactRef(id=artifact_id, characters=len(whole))
        bounded = ToolResultBlock(
            tool_use_id=result.tool_use_id, parts=parts, is_error=result.is_error
        )
        fields = {name: getattr(step, name) for name in Step.model_fields}
        return Step.model_validate(
            {
                **fields,
                "header": header.model_copy(update={"artifact": handle}),
                "content": Content(blocks=(bounded,)),
            }
        )

    async def get_artifact(
        self, ctx: TenantContext, session_id: UUID, artifact_id: UUID, offset: int, limit: int
    ) -> ArtifactPage:
        ctx.require(Permission.READ)
        artifact = await self._storage.read_artifact(ctx.org_id, session_id, artifact_id)
        if artifact is None:
            raise NotFound(f"artifact {artifact_id} not found")
        key = rules.artifact_key(session_id, artifact_id)
        data = await self._buckets.get(ctx.org_id, BUCKET, key, deadline=ctx.deadline)
        text = data.decode("utf-8")
        page, more = rules.artifact_page(text, offset, limit, self._policy)
        return ArtifactPage(
            artifact_id=artifact_id,
            offset=max(0, offset),
            text=page,
            characters=len(text),
            has_more=more,
        )

    def _main(
        self,
        steps: Sequence[Step],
        kind: KindPrompts,
        fill: Fill,
        fill_set_version: int,
        plan: str | None,
    ) -> rules.Draft:
        if rules.open_use(steps, rules.exchanges(steps)) is not None:
            raise PreconditionFailed("a tool call is still open; its response comes first")
        return _drafted(
            lambda: rules.render_main(steps, kind, fill, fill_set_version, plan, self._policy)
        )

    async def _hashed(
        self, ctx: TenantContext, session_id: UUID, draft: rules.Draft
    ) -> RenderedRequest:
        value = rules.prompt_bytes(draft.call, draft.attachments)
        return RenderedRequest(
            call=draft.call,
            window=draft.window,
            delivers=draft.delivers,
            attachments=draft.attachments,
            prompt_hash=await self._hashes.keyed_hash(ctx, session_id, value),
        )

    async def _history(self, ctx: TenantContext, session_id: UUID) -> list[Step]:
        """The session's whole history, in `seq` order."""
        steps: list[Step] = []
        while True:
            after = steps[-1].seq if steps else 0
            page = await self._steps.get_steps(ctx, session_id, after, self._options.page)
            steps.extend(page.items)
            if not page.has_more or not page.items:
                return steps

    async def _compact(
        self,
        ctx: TenantContext,
        session_id: UUID,
        epoch: int,
        loop_id: UUID,
        steps: Sequence[Step],
        fill_set: FillSet,
    ) -> list[Step] | None:
        """Folds the oldest part of the main window into a summary and
        answers the history with it, or None, with nothing written, when
        nothing can be folded. The summarizer's call passes the gate first,
        and its request is persisted before the call; its reply is a
        response, and the summary references it and the range it stands
        for. A reply cut, refused, or empty is recorded and is
        `CompactionFailed`, and no summary is written."""
        summarizer = _fill(fill_set, SUMMARIZER)
        cut = rules.fold_cut(steps, _fill(fill_set, MAIN), summarizer, self._policy)
        if cut is None:
            return None
        call = _drafted(lambda: rules.summarizer_call(steps, cut, summarizer, self._policy))
        first, last = rules.fold_range(steps, cut)
        previous = rules.latest_summary(steps)
        window = ContextWindow(
            role=SUMMARIZER,
            fill=summarizer.name,
            fill_set_version=fill_set.version,
            max_tokens=summarizer.context_window,
            used_tokens=rules.tokens(len(rules.prompt_bytes(call, ())), self._policy),
            left_edge=steps[rules.window_start(steps, previous)].seq,
            right_edge=last,
            summary_id=None if previous is None else previous.id,
        )
        draft = rules.Draft(call, window, (), ())
        rendered = await self._hashed(ctx, session_id, draft)
        request = rules.request_step(rendered, session_id, loop_id, new_id(), utcnow())
        hold = await self._gate.authorize(ctx, session_id, SUMMARIZER, summarizer, call)
        try:
            (request,) = await self._steps.append_steps(ctx, session_id, epoch, [request])
        except BaseException:
            await self._gate.settle(ctx, hold, None, billed=False)
            raise
        try:
            reply = await reply_of(self._providers.get(summarizer.provider).stream(call))
        except ModelCallFailed as failed:
            # Nothing streamed back: the call was never sent, or the provider
            # refused it before processing it, so the hold is released. A
            # stream that broke after it began is billed.
            await self._gate.settle(ctx, hold, None, billed=failed.partial is not None)
            if failed.partial is not None:
                await self._steps.append_steps(
                    ctx, session_id, epoch, [_response(request, failed.partial)]
                )
            raise
        await self._gate.settle(ctx, hold, reply.usage, billed=True)
        response = _response(request, reply)
        whole = (
            not reply.truncated
            and reply.stop_reason is StopReason.END_TURN
            and bool(response.as_text().strip())
            and not response.as_tool_uses()
        )
        if not whole:
            await self._steps.append_steps(ctx, session_id, epoch, [response])
            why = reply.stop_reason.value if reply.stop_reason is not None else "a broken stream"
            raise CompactionFailed(f"the summarizer's reply is not a whole summary ({why})")
        summary = Step(
            id=new_id(),
            created_at=utcnow(),
            session_id=session_id,
            loop_id=loop_id,
            type=StepType.SUMMARY,
            actor=Actor.ENGINE,
            origin=Origin.ENGINE,
            refs=(response.id,),
            header=SummaryHeader(first_seq=first, last_seq=last),
        )
        stored = await self._steps.append_steps(ctx, session_id, epoch, [response, summary])
        return [*steps, request, *stored]


def _fill(fill_set: FillSet, role: ModelRole) -> Fill:
    fill = fill_set.fill_for(role)
    if fill is None:
        raise ValidationFailed(f"the session's fill set names no {role}")
    return fill


def _drafted[T](render: Callable[[], T]) -> T:
    """A render's refusal of its inputs (a tool twice, a schema the kind
    lacks) as the caller's to fix."""
    try:
        return render()
    except ValueError as refused:
        raise ValidationFailed(str(refused)) from refused


def _response(request: Step, reply: ModelReply) -> Step:
    """The summarizer's reply as its request's response, whole or cut."""
    return Step(
        id=new_id(),
        created_at=utcnow(),
        session_id=request.session_id,
        loop_id=request.loop_id,
        type=StepType.MODEL_RESPONSE,
        actor=Actor.MODEL,
        origin=Origin.ENGINE,
        responds_to=request.id,
        header=ModelResponseHeader(truncated=reply.truncated, usage=reply.usage),
        content=Content(blocks=reply.blocks),
        children=Children(thinking=reply.thinking),
    )
