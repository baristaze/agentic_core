from collections.abc import Callable, Sequence
from datetime import datetime
from uuid import UUID

from pydantic import Field

from acme.infra.buckets import Buckets, BucketsInterface
from acme.integrations.model_providers import reply_of
from acme.integrations.model_providers.calls import ModelReply
from acme.integrations.model_providers.failures import ModelCallFailed
from acme.integrations.model_providers.types import StopReason
from acme.om.attribution import AttributionManagerInterface
from acme.om.base import Platform, derived_id, new_id, utcnow
from acme.om.budgets.rules import elapsed_ms
from acme.om.budgets.types.usage import CallSite
from acme.om.context import Permission, TenantContext
from acme.om.exceptions import (
    CompactionFailed,
    ContextOverflow,
    KeyRevoked,
    NotFound,
    PreconditionFailed,
    ValidationFailed,
)
from acme.om.models.credentials import CallCredentialsInterface
from acme.om.models.manager import ModelsManagerInterface
from acme.om.models.types.fill import MAIN, SUMMARIZER, Fill, FillSet, ModelRole
from acme.om.steps import StepsManagerInterface
from acme.om.steps.types.content import Block, Children, Content, TextBlock, ToolResultBlock
from acme.om.steps.types.header import (
    ArtifactRef,
    InputHeader,
    ModelResponseHeader,
    StepHeader,
    SummaryHeader,
    ToolResponseHeader,
)
from acme.om.steps.types.step import Actor, Origin, Step, StepType
from acme.om.tenancy import TenancyManagerInterface
from acme.om.windows import rules
from acme.om.windows.gate import CallGateInterface
from acme.om.windows.hashes import PromptHashInterface
from acme.om.windows.manager import WindowsManagerInterface
from acme.om.windows.seal import ArtifactSealInterface
from acme.om.windows.storage import WindowStorageInterface
from acme.om.windows.types.artifact import Artifact, ArtifactPage
from acme.om.windows.types.kind import KindPrompts
from acme.om.windows.types.policy import CompactionPolicy
from acme.om.windows.types.window import ContextWindow, RenderedRequest

BUCKET = Buckets.ARTIFACTS
CONTENT_TYPE = "application/octet-stream"
"""An artifact's text is sealed before it is stored, so the store holds
bytes no reader can take for text."""


class WindowsOptions(Platform):
    page: int = Field(default=200, gt=0)  # steps one read of the history asks for
    purge_batch: int = Field(default=200, gt=0)  # artifacts one read of a purge takes


class WindowsManagerImpl(WindowsManagerInterface):
    def __init__(
        self,
        storage: WindowStorageInterface,
        steps: StepsManagerInterface,
        tenancy: TenancyManagerInterface,
        models: ModelsManagerInterface,
        attribution: AttributionManagerInterface,
        credentials: CallCredentialsInterface,
        buckets: BucketsInterface,
        gate: CallGateInterface,
        hashes: PromptHashInterface,
        seal: ArtifactSealInterface,
        policy: CompactionPolicy,
        options: WindowsOptions,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        """`credentials` answers the client a compaction's call runs on: the
        platform's key, or a tenant's."""
        self._storage = storage
        self._steps = steps
        self._tenancy = tenancy
        self._models = models
        self._attribution = attribution
        self._credentials = credentials
        self._buckets = buckets
        self._gate = gate
        self._hashes = hashes
        self._seal = seal
        self._policy = policy
        # The sealed artifacts of sessions that keep no content at rest, by
        # tenant, session, and id: held while this runtime lives, and
        # nowhere else; noise once the session's key is revoked.
        self._held: dict[tuple[UUID, UUID, UUID], bytes] = {}
        self._options = options
        self._clock = clock

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
        history: Sequence[Step] | None = None,
    ) -> RenderedRequest:
        ctx.require(Permission.WRITE)
        fill_set = await self._models.get_fill_set(ctx, session_id)
        fill = _fill(fill_set, role)
        steps = await self._history(ctx, session_id) if history is None else history
        if role != MAIN:
            draft = _drafted(
                lambda: rules.render_side(steps, kind, role, fill, fill_set.version, self._policy)
            )
            return await self._hashed(ctx, session_id, draft, steps)
        draft = self._main(steps, kind, fill, fill_set.version, plan)
        read: Sequence[Step] = steps
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
                read = compacted
        return await self._hashed(ctx, session_id, draft, read)

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
        history: Sequence[Step] | None = None,
    ) -> RenderedRequest:
        ctx.require(Permission.WRITE)
        if refused.overflow_retry:
            raise ContextOverflow("the provider refused the request's one retry as too long")
        if refused.window.role != MAIN:
            raise ContextOverflow(f"a {refused.window.role} request reads a suffix; none compacts")
        fill_set = await self._models.get_fill_set(ctx, session_id)
        fill = _fill(fill_set, MAIN)
        steps = await self._history(ctx, session_id) if history is None else history
        self._main(steps, kind, fill, fill_set.version, plan)
        compacted = await self._compact(ctx, session_id, epoch, loop_id, steps, fill_set)
        if compacted is None:
            raise ContextOverflow("the window holds nothing more to fold")
        draft = self._main(compacted, kind, fill, fill_set.version, plan)
        rendered = await self._hashed(ctx, session_id, draft, compacted)
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
        handle = await self._keep(ctx, session_id, step, whole)
        bounded = ToolResultBlock(
            tool_use_id=result.tool_use_id, parts=parts, is_error=result.is_error
        )
        return _bounded(step, header.model_copy(update={"artifact": handle}), (bounded,))

    async def bound_report(self, ctx: TenantContext, session_id: UUID, step: Step) -> Step:
        ctx.require(Permission.WRITE)
        header = step.header
        reported = isinstance(header, InputHeader) and step.actor is Actor.AGENT
        if not reported or step.session_id != session_id:
            raise ValidationFailed(f"a {step.type.value} step is no agent's input of {session_id}")
        assert isinstance(header, InputHeader)
        if header.artifact is not None:
            return step
        whole = step.as_text()
        kept = rules.clip(whole, self._policy)
        if kept is None:
            return step
        handle = await self._keep(ctx, session_id, step, whole)
        others = tuple(block for block in step.content.blocks if not isinstance(block, TextBlock))
        return _bounded(step, header.model_copy(update={"artifact": handle}), (*kept, *others))

    async def _keep(
        self, ctx: TenantContext, session_id: UUID, step: Step, whole: str
    ) -> ArtifactRef:
        """A step's whole text kept as an artifact of its session, and its
        handle. Its id is derived from the step, so a run that keeps the
        same step twice writes the same artifact once. It is sealed like the
        step it came from. A session that keeps no content at rest keeps its
        artifact in this runtime's memory alone, as it keeps its steps'
        content, and its step is bounded all the same."""
        artifact_id = derived_id(step.id, step.created_at, "artifact")
        sealed = await self._seal.seal(ctx, session_id, artifact_id, whole.encode("utf-8"))
        if not sealed.at_rest:
            self._held[(ctx.org_id, session_id, artifact_id)] = sealed.blob
        else:
            await self._buckets.put(
                ctx.org_id,
                BUCKET,
                rules.artifact_key(session_id, artifact_id),
                sealed.blob,
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
        return ArtifactRef(id=artifact_id, characters=len(whole))

    async def get_artifact(
        self, ctx: TenantContext, session_id: UUID, artifact_id: UUID, offset: int, limit: int
    ) -> ArtifactPage:
        ctx.require(Permission.READ)
        text = await self._kept(ctx, session_id, artifact_id)
        page, more = rules.artifact_page(text, offset, limit, self._policy)
        return ArtifactPage(
            artifact_id=artifact_id,
            offset=max(0, offset),
            text=page,
            characters=len(text),
            has_more=more,
        )

    async def _kept(self, ctx: TenantContext, session_id: UUID, artifact_id: UUID) -> str:
        """An artifact's text, held in this runtime's memory or kept in the
        store, opened under its session's key. Once the key is revoked it is
        noise wherever it is, and every read of it is refused."""
        held = (ctx.org_id, session_id, artifact_id)
        sealed = self._held.get(held)
        if sealed is None:
            artifact = await self._storage.read_artifact(ctx.org_id, session_id, artifact_id)
            if artifact is None:
                raise NotFound(f"artifact {artifact_id} not found")
            key = rules.artifact_key(session_id, artifact_id)
            sealed = await self._buckets.get(ctx.org_id, BUCKET, key, deadline=ctx.deadline)
        data = await self._seal.open(ctx, session_id, artifact_id, sealed)
        if data is None:
            raise KeyRevoked(f"the content of artifact {artifact_id} is erased with its key")
        return data.decode("utf-8")

    async def purge_artifacts(self, org_id: UUID, session_id: UUID) -> int:
        return await self._purge(org_id, session_id)

    async def purge_tenant(self, ctx: TenantContext) -> int:
        ctx.require(Permission.WRITE)
        if not await self._tenancy.tenant_expired(ctx):
            return 0
        return await self._purge(ctx.org_id, None)

    async def _purge(self, org_id: UUID, session_id: UUID | None) -> int:
        """The session's artifacts, or the tenant's, a batch at a time: each
        object first, then the records, so a failure between the two leaves
        a record the next pass finds, never an object no record names. A
        tenant's purge takes one batch a call, as every sweep step does; a
        session's takes them all, since its row goes once this returns. What
        this runtime holds in memory of them goes first."""
        for held in [k for k in self._held if k[0] == org_id and session_id in (None, k[1])]:
            del self._held[held]
        purged = 0
        while True:
            batch = await self._storage.read_artifacts(
                org_id, session_id, self._options.purge_batch
            )
            for artifact in batch:
                key = rules.artifact_key(artifact.session_id, artifact.id)
                await self._buckets.delete(org_id, BUCKET, key)
            purged += await self._storage.purge_artifacts(org_id, [a.id for a in batch])
            if session_id is None or len(batch) < self._options.purge_batch:
                return purged

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
        self, ctx: TenantContext, session_id: UUID, draft: rules.Draft, read: Sequence[Step]
    ) -> RenderedRequest:
        """The request over `read`, the steps it was rendered from, with its
        prompt's keyed hash, and who spoke and who pays, read from exactly
        the inputs it delivers: one read gives both, so an input that lands
        after it is neither delivered nor credited."""
        value = rules.prompt_bytes(draft.call, draft.attachments)
        delivers = set(draft.delivers)
        delivered = {step.id: step.seq for step in read if step.id in delivers}
        said = await self._attribution.attribute_request(
            ctx, session_id, rules.latest_request_seq(read), delivered
        )
        return RenderedRequest(
            call=draft.call,
            window=draft.window,
            delivers=draft.delivers,
            attachments=draft.attachments,
            prompt_hash=await self._hashes.keyed_hash(ctx, session_id, value),
            attribution=said,
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
        rendered = await self._hashed(ctx, session_id, draft, steps)
        # The summarizer delivers nothing: it is paid for by the spender the
        # latest model request named, as attribution answers for it.
        paid = rendered.attribution
        used = await self._credentials.client_for(ctx, summarizer.provider)
        hold = await self._gate.authorize(
            ctx, session_id, paid.spender, SUMMARIZER, summarizer, call, credential=used.credential
        )
        request = rules.request_step(
            rendered, paid, session_id, loop_id, new_id(), self._clock(), hold_id=hold
        )
        try:
            (request,) = await self._steps.append_steps(ctx, session_id, epoch, [request])
        except BaseException:
            await self._gate.settle(ctx, hold, None, billed=False, site=None)
            raise
        started = self._clock()
        try:
            reply = await reply_of(used.client.stream(call))
        except ModelCallFailed as failed:
            # The failure names the key the call went out on, so whatever it
            # says of a key is said of that one, whichever is live by then.
            failed.credential = used.credential
            # Nothing streamed back: the call was never sent, or the provider
            # refused it before processing it, so the hold is released. A
            # stream that broke after it began is billed, whole.
            if failed.partial is None:
                await self._gate.settle(ctx, hold, None, billed=False, site=None)
                raise
            broken = _response(request, failed.partial, self._clock())
            site = CallSite(
                loop_id=loop_id,
                step_id=broken.id,
                latency_ms=elapsed_ms(started, broken.created_at),
            )
            await self._gate.settle(
                ctx, hold, None, billed=True, site=site, partial=failed.partial.usage
            )
            await self._steps.append_steps(ctx, session_id, epoch, [broken])
            raise
        answered = self._clock()
        response = _response(request, reply, answered)
        site = CallSite(
            loop_id=loop_id, step_id=response.id, latency_ms=elapsed_ms(started, answered)
        )
        await self._gate.settle(ctx, hold, reply.usage, billed=True, site=site)
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
            created_at=self._clock(),
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


def _bounded(step: Step, header: StepHeader, blocks: tuple[Block, ...]) -> Step:
    """`step` holding `header` and `blocks` in place of its own."""
    fields = {name: getattr(step, name) for name in Step.model_fields}
    return Step.model_validate({**fields, "header": header, "content": Content(blocks=blocks)})


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


def _response(request: Step, reply: ModelReply, at: datetime) -> Step:
    """The summarizer's reply as its request's response, whole or cut, with
    why the provider stopped."""
    return Step(
        id=new_id(),
        created_at=at,
        session_id=request.session_id,
        loop_id=request.loop_id,
        type=StepType.MODEL_RESPONSE,
        actor=Actor.MODEL,
        origin=Origin.ENGINE,
        responds_to=request.id,
        header=ModelResponseHeader(
            truncated=reply.truncated, usage=reply.usage, stop_reason=reply.stop_reason
        ),
        content=Content(blocks=reply.blocks),
        children=Children(thinking=reply.thinking),
    )
