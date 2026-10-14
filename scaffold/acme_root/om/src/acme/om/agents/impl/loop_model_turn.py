"""A turn of the model: one call, rendered from the history, passed
through the budget gate, streamed, and persisted before anything acts on
it; and a provider's error, retried, fallen back from, or parked on."""

import contextlib
import itertools
import logging
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from acme.infra.base import SYSTEM_SCOPE
from acme.infra.outages import Outage
from acme.integrations.model_providers import ModelProviderInterface
from acme.integrations.model_providers.calls import Finished, ModelCall, ModelReply
from acme.integrations.model_providers.failures import ModelCallFailed
from acme.integrations.model_providers.types import ErrorAnswer, ErrorKind, StopReason
from acme.om.agent_steps.types.agent_step import AgentStep
from acme.om.agent_steps.types.header import (
    LoopOutcome,
    ModelResponseHeader,
    Park,
    ParkReason,
)
from acme.om.agents import loop_rules as rules
from acme.om.agents.impl.loop_core import (
    LoopCore,
    RunState,
)
from acme.om.agents.types.run import LoopRun
from acme.om.base import new_id
from acme.om.context import TenantContext
from acme.om.exceptions import (
    CompactionFailed,
    ContextOverflow,
    GateParked,
    NoCredential,
    NoSpender,
    PlatformException,
    SpendBudgetRefused,
    SpenderUnknown,
    Unavailable,
)
from acme.om.language_models.credentials import CallClient
from acme.om.language_models.types.fill import MAIN, SUMMARIZER, Fill
from acme.om.spend_budgets.rules import elapsed_ms, spend_budget_park
from acme.om.spend_budgets.types.usage import CallSite
from acme.om.tools.native.load_toolset import loads
from acme.om.tools.native.write_agent_plan import current_agent_plan
from acme.om.windows.rules import request_step
from acme.om.windows.types.window import RenderedRequest

log = logging.getLogger(__name__)


PERMISSION = "permission"
"""What a provider park's unlock names for a credential error that is no
refusal of the key: a permission, a region, or a model the key cannot
reach."""


class LoopModelTurn(LoopCore):
    """A turn of the model: its fill and key, the outage mark, the request
    rendered, the call through the gate, and what a provider error
    comes to."""

    async def _model_turn(self, run: RunState, history: Sequence[AgentStep]) -> LoopRun | None:
        ctx = run.ctx
        fill_set = await self._language_models.get_fill_set(ctx, run.agent_session_id)
        fill = fill_set.fill_for(MAIN)
        if fill is None:
            return await self._end(run, LoopOutcome.ERRORED)
        try:
            # The key the call goes out on, the tenant's own when it pays its
            # provider: read per call, so a rotated key is never used.
            used = await self._credentials.client_for(ctx, fill.provider)
        except PlatformException as refused:
            return await self._refused(run, refused)
        # A provider marked failing for this credential parks the loop at
        # once, before anything is rendered, held, or spent, until the mark's
        # retry time by the loop's own clock.
        outage = await self._outages.current(
            self._holder(ctx, used.credential), fill.provider.value, used.credential
        )
        if outage is not None and outage.retry_at > self._clock():
            return await self._park(run, provider_park(fill, outage.retry_at))
        # The tools this request offers: the history's loads included, so a
        # load settled since the last request adds its tools here, and never
        # to a call of the turn that made it.
        run.registry = self._registry(run.session, run.kind, history, run.attached)
        prompts = rules.kind_prompts(
            run.kind, run.registry, loads(history), rules.standing_text(history, run.start_seq)
        )
        # The agent's current plan, read off the whole history, so a summary
        # that folded the call that wrote it still leaves it in view.
        kept = current_agent_plan(history)
        plan = None if kept is None else kept.text
        try:
            if run.refused is not None:
                rendered = await self._windows.render_after_overflow(
                    ctx,
                    run.agent_session_id,
                    run.epoch,
                    run.loop_id,
                    prompts,
                    run.refused,
                    plan=plan,
                    history=history,
                )
            else:
                rendered = await self._windows.render_request(
                    ctx,
                    run.agent_session_id,
                    run.epoch,
                    run.loop_id,
                    prompts,
                    MAIN,
                    plan=plan,
                    history=history,
                )
        except ModelCallFailed as failed:
            # The compaction's call to the summarizer failed: its own
            # provider's error, on the key that call went out on, which no
            # switch of the main role mends.
            summarizer = fill_set.fill_for(SUMMARIZER) or fill
            return await self._failed(run, summarizer, None, failed, failed.credential)
        except PlatformException as refused:
            return await self._refused(run, refused)
        try:
            replied = await self._call(run, fill, rendered, used)
        except ModelCallFailed as failed:
            return await self._failed(run, fill, rendered, failed, used.credential)
        except PlatformException as refused:
            return await self._refused(run, refused)
        run.failures, run.refused = 0, None
        run.tried.clear()
        # The provider answered on this key: a mark on the pair is over, so no
        # other session parks on it.
        await self._outages.clear(
            self._holder(ctx, used.credential), fill.provider.value, used.credential
        )
        header = replied.header
        if (
            isinstance(header, ModelResponseHeader)
            and header.stop_reason is StopReason.OUTPUT_LIMIT
        ):
            # Cut by its output bound: kept truncated and never acted on,
            # and the model is told so, so the next request is not this one
            # again.
            await self._notice(run, rules.CUT)
        return None

    async def _refused(self, run: RunState, refused: PlatformException) -> LoopRun:
        """What stops a model turn before its call is made: the gate's refusal
        parks on the budget, a gate that parks the call parks where it says,
        nobody to pay parks for a person, and a window that cannot be read
        ends the loop. Anything else is not the loop's to answer."""
        match refused:
            case SpendBudgetRefused():
                return await self._park(run, spend_budget_park(refused.refusal, self._clock()))
            case GateParked():
                return await self._park(run, refused.park)
            case NoSpender() | SpenderUnknown():
                log.info("session %s: %s", run.agent_session_id, refused.message)
                park = Park(reason=ParkReason.PERSON, unlock=rules.SPENDER_UNLOCK)
                return await self._park(run, park)
            case NoCredential():
                # The tenant's key for the provider is missing or refused:
                # nothing runs on another, and a key saved unlocks it.
                log.info("session %s: %s", run.agent_session_id, refused.message)
                provider, _, cause = refused.unlock.partition(":")
                park = Park(
                    reason=ParkReason.PROVIDER,
                    unlock=refused.unlock,
                    cause=rules.fix_cause(provider, cause, tenants=True),
                )
                return await self._park(run, park)
            case CompactionFailed() | ContextOverflow() | Unavailable():
                log.warning("session %s: %s", run.agent_session_id, refused.message)
                return await self._end(run, LoopOutcome.ERRORED)
            case _:
                raise refused

    async def _call(
        self, run: RunState, fill: Fill, rendered: RenderedRequest, used: CallClient
    ) -> AgentStep:
        """One model call: who spoke and who pays, the gate's hold, the request
        persisted, the stream emitted as it arrives, and the response
        persisted before anything acts on it. The call goes out on `used`,
        and the gate holds it knowing which key that is."""
        ctx, agent_session_id = run.ctx, run.agent_session_id
        said = rendered.attribution
        hold = await self._gate.authorize(
            ctx,
            agent_session_id,
            said.spender,
            MAIN,
            fill,
            rendered.call,
            credential=used.credential,
        )
        request = request_step(
            rendered,
            said,
            agent_session_id,
            run.loop_id,
            new_id(),
            self._clock(),
            spend_hold_id=hold,
        )
        try:
            (request,) = await self._steps.append_agent_steps(
                ctx, agent_session_id, run.epoch, [request]
            )
        except BaseException:
            # Never sent: nothing was billed.
            await self._gate.settle(ctx, hold, None, billed=False, site=None)
            raise
        response_id = new_id()
        self._tell(run, self._sink.opened, response_id)
        started = self._clock()

        def site() -> CallSite:
            elapsed = elapsed_ms(started, self._clock())
            return CallSite(loop_id=run.loop_id, agent_step_id=response_id, latency_ms=elapsed)

        try:
            try:
                reply = await self._stream(run, used.client, rendered.call, response_id)
            except ModelCallFailed as failed:
                # Nothing streamed back: the call was refused before it was
                # processed, and the hold is released. A stream that broke is
                # usually billed, so it counts whole, and what arrived is kept.
                if failed.partial is None:
                    await self._gate.settle(ctx, hold, None, billed=False, site=None)
                    closing = rules.abandoned_step(response_id, self._clock(), request)
                else:
                    await self._gate.settle(
                        ctx, hold, None, billed=True, site=site(), partial=failed.partial.usage
                    )
                    closing = rules.response_step(
                        response_id, self._clock(), request, failed.partial
                    )
                await self._steps.append_agent_steps(ctx, agent_session_id, run.epoch, [closing])
                raise
            except BaseException:
                with contextlib.suppress(Exception):
                    await self._gate.settle(ctx, hold, None, billed=True, site=site())
                raise
            await self._gate.settle(ctx, hold, reply.usage, billed=True, site=site())
            stored = rules.response_step(response_id, self._clock(), request, reply)
            (stored,) = await self._steps.append_agent_steps(
                ctx, agent_session_id, run.epoch, [stored]
            )
            return stored
        finally:
            # Over, whichever way: its step is stored, or its call failed.
            self._tell(run, self._sink.completed, response_id)

    async def _stream(
        self, run: RunState, client: ModelProviderInterface, call: ModelCall, response_id: UUID
    ) -> ModelReply:
        """The provider's stream, each part emitted as it arrives, never
        waited on; the reply is the one its last part carries."""
        numbers = itertools.count()
        async for part in client.stream(call):
            if isinstance(part, Finished):
                return part.reply
            emitted = rules.stream_part(run.agent_session_id, response_id, next(numbers), part)
            if emitted is not None:
                self._emit(run, emitted)
        raise ModelCallFailed(ErrorKind.TRANSIENT, "the stream ended with no reply")

    def _holder(self, ctx: TenantContext, credential: str) -> UUID:
        """The org that holds the credential, as the outage signal keys it:
        the system scope for the platform's own key, the tenant for its own."""
        return SYSTEM_SCOPE if credential == self._options.credential else ctx.org_id

    async def _failed(
        self,
        run: RunState,
        fill: Fill,
        rendered: RenderedRequest | None,
        failed: ModelCallFailed,
        credential: str | None,
    ) -> LoopRun | None:
        """A provider error, handled by its kind. `rendered` is the main
        request that failed, or None when the compaction's call did, which
        neither falls back nor compacts again; `credential` names the key the
        call went out on, so an outage or a refusal is that key's alone, and
        None, when no call says, marks neither. None goes on to the next
        model turn."""
        answer = failed.kind.answer
        now = self._clock()
        main = rendered is not None
        if answer is ErrorAnswer.RETRY:
            wait = rules.retry_wait(
                failed.retry_after, run.failures, self._options.retry_base, self._jitter()
            )
            run.failures += 1
            if run.failures <= self._options.retries and wait <= self._options.retry_cap:
                await self._sleep(wait.total_seconds())
                return None
            # The retries are spent: the provider is failing for this
            # credential. The mark tells every session that would call it, in
            # every process on the shared cache, and each parks at once until
            # the retry time; this one falls back to its next declared
            # fallback when it has one, and parks too when not.
            retry_at = now + max(wait, self._options.outage_wait)
            if credential is not None:
                outage = Outage(
                    org_id=self._holder(run.ctx, credential),
                    provider=fill.provider.value,
                    credential=credential,
                    retry_at=retry_at,
                )
                await self._outages.mark(outage)
            if main and await self._fall_back(run, fill):
                return None
            return await self._park(run, provider_park(fill, retry_at))
        if answer is ErrorAnswer.COMPACT:
            if rendered is None or rendered.overflow_retry:
                return await self._end(run, LoopOutcome.ERRORED)
            run.refused = rendered
            return None
        if answer is ErrorAnswer.RESOLVE:
            if main and await self._fall_back(run, fill):
                return None
            return await self._end(run, LoopOutcome.ERRORED)
        if answer is ErrorAnswer.PARK:
            cause = failed.kind.value
            if failed.key_refused and credential is not None:
                # The provider did not take the key itself: every session
                # that needs it waits for a new one, and no other moves.
                await self._credentials.refused(run.ctx, fill.provider, credential)
            elif failed.kind is ErrorKind.CREDENTIAL:
                # A permission the key lacks, a region it refuses, a model it
                # cannot reach: this call's alone, so this session alone waits.
                cause = PERMISSION
            # Whoever holds the key fixes it at the provider, or saves a new
            # one, and nothing tells the engine: the park tries again by
            # itself, so a fix wakes it, and says who must act.
            tenants = credential is not None and credential != self._options.credential
            park = Park(
                reason=ParkReason.PROVIDER,
                unlock=f"{fill.provider.value}:{cause}",
                retry_at=now + self._options.fix_wait,
                cause=rules.fix_cause(fill.provider.value, cause, tenants),
            )
            return await self._park(run, park)
        log.warning("session %s: %s", run.agent_session_id, failed)
        return await self._end(run, LoopOutcome.ERRORED)

    async def _fall_back(self, run: RunState, fill: Fill) -> bool:
        """A switch of the main role to its next declared fallback the session
        admits and no call of this run has tried; False when none is left."""
        switched = await self._language_models.fall_back(
            run.ctx, run.agent_session_id, run.epoch, run.loop_id, MAIN, (*run.tried, fill)
        )
        if switched is None:
            return False
        run.tried.append(fill)
        run.failures = 0
        return True


def provider_park(fill: Fill, retry_at: datetime) -> Park:
    """The park of a provider that is failing: it names the provider and
    tries again by itself at the retry time."""
    return Park(reason=ParkReason.PROVIDER, unlock=fill.provider.value, retry_at=retry_at)
