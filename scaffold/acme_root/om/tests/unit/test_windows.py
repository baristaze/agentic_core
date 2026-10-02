"""The windows swimlane over the memory storage and the scripted provider: a
window near its limit compacts into a summary step that references its
range, a provider's overflow compacts once and retries once, compaction
never loops and never spends outside the gate, a recorded request
re-renders to its prompt's hash, and a tool result over the bound is kept as
an artifact the step holds the head, the tail, and the handle of."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from contracts.doubles import context
from contracts.histories import AT, MAIN_FILL, History

from acme.infra.impl.local import InfraLocalImpl
from acme.integrations.model_providers import reply_of
from acme.integrations.model_providers.calls import ModelCall, ModelReply, ToolSpec
from acme.integrations.model_providers.failures import ModelCallFailed
from acme.integrations.model_providers.registry import scripted_model_providers
from acme.integrations.model_providers.scripted import ModelProviderScriptedImpl, ScriptedFailure
from acme.integrations.model_providers.types import ErrorKind, ProviderName, StopReason, Usage
from acme.om.agent_sessions.types.agent_session import AgentSession
from acme.om.attribution import AttributionManagerInterface
from acme.om.attribution.types.authority import AuthorityMode, RequestAttribution
from acme.om.attribution.types.principal import Principal, PrincipalKind
from acme.om.base import new_id, utcnow
from acme.om.context import Role, TenantContext
from acme.om.exceptions import (
    CompactionFailed,
    ContextOverflow,
    NotFound,
    PreconditionFailed,
    StaleWriter,
    Unavailable,
    ValidationFailed,
)
from acme.om.models.impl.manager import ModelsManagerImpl, ModelsOptions
from acme.om.models.impl.resolver import ModelResolverTableImpl, ResolverOptions
from acme.om.models.prices import ModelPricesInterface
from acme.om.models.types.fill import (
    MAIN,
    SUMMARIZER,
    Eligibility,
    Fill,
    ModelRole,
    RoleFill,
    SwitchReason,
)
from acme.om.privacy.impl.artifacts import ArtifactSealKeysImpl
from acme.om.privacy.impl.keys import SessionKeysImpl
from acme.om.root import Managers, build_managers
from acme.om.steps import StepsManagerInterface
from acme.om.steps.types.content import Content, TextBlock, ToolResultBlock
from acme.om.steps.types.header import (
    ArtifactRef,
    ControlCommand,
    ModelRequestHeader,
    ModelResponseHeader,
    SummaryHeader,
    ToolResponseHeader,
)
from acme.om.steps.types.step import Step, StepType
from acme.om.storage.impl.memory import StorageMemoryImpl
from acme.om.windows import rules
from acme.om.windows.gate import CallGateInterface
from acme.om.windows.impl.gate import CallGateNullImpl
from acme.om.windows.impl.hashes import PromptHashMemoryImpl, PromptHashNullImpl
from acme.om.windows.impl.manager import WindowsManagerImpl, WindowsOptions
from acme.om.windows.types.kind import KindPrompts
from acme.om.windows.types.policy import CompactionPolicy

WIDE = Fill(
    provider=ProviderName.ANTHROPIC,
    model="claude-sonnet-5-5",
    max_output_tokens=200,
    context_window=40_000,
)
NARROW = Fill(
    provider=ProviderName.ANTHROPIC,
    model="claude-sonnet-5-5-narrow",
    max_output_tokens=200,
    context_window=2_000,
)
SUMMARY_FILL = Fill(
    provider=ProviderName.OPENAI,
    model="gpt-6-luna",
    max_output_tokens=300,
    context_window=8_000,
)
TRIAGE = Fill(
    provider=ProviderName.OPENAI,
    model="gpt-6-luna-mini",
    max_output_tokens=100,
    context_window=1_200,
)
NARROWING = (
    RoleFill(role=MAIN, fill=NARROW, fallbacks=(WIDE,)),
    RoleFill(role=SUMMARIZER, fill=SUMMARY_FILL),
    RoleFill(role="triage", fill=TRIAGE),
)
WIDENING = (
    RoleFill(role=MAIN, fill=WIDE, fallbacks=(NARROW,)),
    RoleFill(role=SUMMARIZER, fill=SUMMARY_FILL),
    RoleFill(role="triage", fill=TRIAGE),
)
KIND = KindPrompts(
    kind="investigator",
    version=1,
    prompts=("You investigate faults.",),
    tools=(ToolSpec(name="read_log"),),
)
SUMMARY = "The gripper releases at 4.2 s, before the placement location."
assert MAIN_FILL == WIDE.name


class Priced(ModelPricesInterface):
    def priced(self, provider: ProviderName, model: str) -> bool:
        return True

    def describe(self) -> str:
        return "model prices: every model"


class Gate(CallGateInterface):
    """A gate that records each hold, who pays it, and how it closed, or
    refuses them all."""

    def __init__(self) -> None:
        self.refusing = False
        self.holds: dict[UUID, ModelRole] = {}
        self.spenders: list[Principal] = []
        self.settled: list[tuple[UUID, Usage | None, bool]] = []

    async def authorize(
        self,
        ctx: TenantContext,
        session_id: UUID,
        spender: Principal,
        role: ModelRole,
        fill: Fill,
        call: ModelCall,
    ) -> UUID:
        if self.refusing:
            raise Unavailable("the session's budget is spent")
        hold = new_id()
        self.holds[hold] = role
        self.spenders.append(spender)
        return hold

    async def settle(
        self, ctx: TenantContext, hold_id: UUID, usage: Usage | None, *, billed: bool
    ) -> None:
        assert hold_id in self.holds
        self.settled.append((hold_id, usage, billed))


PAYER = Principal(kind=PrincipalKind.PERSON, id=new_id())


class Payer(AttributionManagerInterface):
    """Just enough attribution for a compaction: who pays, and the range it
    was asked about. A partial double: only `attribute_request` is reached,
    and any other method fails loudly as unimplemented."""

    def __init__(self) -> None:
        self.asked: list[tuple[int, tuple[UUID, ...]]] = []

    async def attribute_request(
        self, ctx: TenantContext, session_id: UUID, after_seq: int, delivered: Mapping[UUID, int]
    ) -> RequestAttribution:
        self.asked.append((after_seq, tuple(delivered)))
        return RequestAttribution(speaker=PAYER, spender=PAYER)


Payer.__abstractmethods__ = frozenset()


@dataclass
class Engine:
    steps: StepsManagerInterface
    models: ModelsManagerImpl
    windows: WindowsManagerImpl
    main: ModelProviderScriptedImpl
    summarizer: ModelProviderScriptedImpl
    gate: Gate
    hashes: PromptHashMemoryImpl
    ctx: TenantContext


def an_engine(tmp_path: Path, table: Sequence[RoleFill] = NARROWING) -> Engine:
    storage = StorageMemoryImpl()
    infra = InfraLocalImpl(tmp_path)
    managers = build_managers(storage, infra)
    models = ModelsManagerImpl(
        storage.get_fill_set_storage(),
        managers.steps,
        managers.tenancy,
        ModelResolverTableImpl(Priced(), ResolverOptions(table=tuple(table))),
        ModelsOptions(),
    )
    providers = scripted_model_providers()
    gate, hashes = Gate(), PromptHashMemoryImpl()
    seal = ArtifactSealKeysImpl(
        SessionKeysImpl(storage.get_privacy_storage(), infra.get_keys()),
        storage.get_privacy_storage(),
    )
    windows = WindowsManagerImpl(
        storage.get_window_storage(),
        managers.steps,
        managers.tenancy,
        models,
        Payer(),  # pyright: ignore[reportAbstractUsage] (a partial double)
        providers,
        infra.get_buckets(),
        gate,
        hashes,
        seal,
        CompactionPolicy(),
        WindowsOptions(page=7),
    )
    return Engine(
        managers.steps,
        models,
        windows,
        cast(ModelProviderScriptedImpl, providers.get(ProviderName.ANTHROPIC)),
        cast(ModelProviderScriptedImpl, providers.get(ProviderName.OPENAI)),
        gate,
        hashes,
        context(Role.MEMBER),
    )


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    return an_engine(tmp_path)


@dataclass
class Session:
    id: UUID
    epoch: int
    loop_id: UUID


async def a_session(engine: Engine, history: History) -> Session:
    """The history stored as a run appends it, under a fill set resolved."""
    epoch = await engine.steps.begin_run(engine.ctx, history.session_id)
    await engine.models.resolve_fill_set(
        engine.ctx, history.session_id, [MAIN, SUMMARIZER, "triage"], Eligibility()
    )
    if history.steps:
        await engine.steps.append_steps(engine.ctx, history.session_id, epoch, history.steps)
    return Session(history.session_id, epoch, history.loop_id or new_id())


def a_long_history(turns: int = 4, size: int = 2_000) -> History:
    history = History()
    objective = history.message("Find why the robot drops the object, and fix it.")
    history.turn((objective,), "Reading the gripper log.", [("read_log", "g" * size)])
    for n in range(turns - 1):
        history.turn((), f"Reading part {n}.", [("read_log", str(n) * size)])
    return history


def a_summary(text: str = SUMMARY, **fields: object) -> ModelReply:
    reply = {
        "blocks": (TextBlock(text=text),),
        "stop_reason": StopReason.END_TURN,
        "usage": Usage(input=900, output=60),
        "model": SUMMARY_FILL.model,
        **fields,
    }
    return ModelReply.model_validate(reply)


async def history_of(engine: Engine, session: Session) -> list[Step]:
    return list((await engine.steps.get_steps(engine.ctx, session.id, 0, 200)).items)


def of_type(steps: Sequence[Step], step_type: StepType) -> list[Step]:
    return [step for step in steps if step.type is step_type]


async def record(engine: Engine, session: Session, rendered: rules.RenderedRequest) -> Step:
    paid = RequestAttribution(speaker=PAYER, spender=PAYER)
    request = rules.request_step(rendered, paid, session.id, session.loop_id, new_id(), utcnow())
    (stored,) = await engine.steps.append_steps(engine.ctx, session.id, session.epoch, [request])
    return stored


# A window near its limit compacts into a summary step.


async def test_a_window_near_its_limit_compacts_into_a_summary_that_references_its_range(
    engine: Engine,
) -> None:
    session = await a_session(engine, a_long_history())
    engine.summarizer.add(a_summary())
    rendered = await engine.windows.render_request(
        engine.ctx, session.id, session.epoch, session.loop_id, KIND
    )
    steps = await history_of(engine, session)
    (summary,) = of_type(steps, StepType.SUMMARY)
    request, reply = steps[-3], steps[-2]
    assert isinstance(request.header, ModelRequestHeader) and request.header.role == SUMMARIZER
    assert request.header.spender == PAYER and engine.gate.spenders == [PAYER], "the payer pays"
    assert request.header.fill == SUMMARY_FILL.name and request.header.prompt_hash
    assert reply.responds_to == request.id and reply.as_text() == SUMMARY
    assert isinstance(reply.header, ModelResponseHeader) and reply.header.usage is not None
    assert summary.refs == (reply.id,), "it references the reply, and copies none of it"
    assert isinstance(summary.header, SummaryHeader)
    span = summary.header
    assert span.first_seq == 1 and span.last_seq < request.seq
    folded = [s for s in steps if s.seq <= span.last_seq]
    assert folded[-1].type is StepType.TOOL_RESPONSE, "cut on a whole exchange"
    assert rendered.window.summary_id == summary.id
    assert rendered.window.left_edge == span.last_seq + 1
    lead = [b.text for b in rendered.call.messages[0].blocks if isinstance(b, TextBlock)]
    assert "<objective" in lead[0] and lead[1].startswith('<data origin="summary"')
    assert SUMMARY in lead[1]
    assert rendered.window.used_tokens < rules.limit_tokens(NARROW, CompactionPolicy())
    ((hold, role),) = engine.gate.holds.items()
    assert role == SUMMARIZER
    assert engine.gate.settled == [(hold, Usage(input=900, output=60), True)]
    (asked,) = engine.summarizer.calls
    assert asked.model == SUMMARY_FILL.model and asked.messages[0].role == "user"
    assert all(b.kind == "text" and b.text.startswith("<data ") for b in asked.messages[0].blocks)


async def test_the_summarizer_is_asked_where_the_agent_stopped_and_for_no_instruction(
    engine: Engine,
) -> None:
    """The default prompt asks for the sections that let the agent go on:
    what it did, found, decided, and left unchecked, and what it was doing
    where the record ends; bounded, invented from nothing, and with no
    instruction of its own."""
    session = await a_session(engine, a_long_history())
    engine.summarizer.add(a_summary())
    await engine.windows.render_request(
        engine.ctx, session.id, session.epoch, session.loop_id, KIND
    )
    (asked,) = engine.summarizer.calls
    prompt = asked.system[0].text
    for section in ("## Done", "## Known", "## Decided", "## State", "## Next"):
        assert f"\n{section}\n" in prompt
    assert "was doing where the record ends" in prompt and "not yet checked" in prompt
    assert "invent nothing" in prompt and "under 1,500 words" in prompt
    assert "write no instruction of your own" in prompt and "do not restate them" in prompt


async def test_a_compacted_window_does_not_compact_again(engine: Engine) -> None:
    session = await a_session(engine, a_long_history())
    engine.summarizer.add(a_summary())
    for _ in range(3):
        await engine.windows.render_request(
            engine.ctx, session.id, session.epoch, session.loop_id, KIND
        )
    assert len(of_type(await history_of(engine, session), StepType.SUMMARY)) == 1
    assert engine.summarizer.remaining == 0 and len(engine.summarizer.calls) == 1


async def test_the_trigger_reads_the_size_the_provider_reported(engine: Engine) -> None:
    history = History()
    objective = history.message("Find it.")
    history.turn((objective,), "Looking.", [("read_log", "a short log")])
    request = history.request(fill=NARROW.name)
    history.response(request, "Still looking.", usage=Usage(input=1_500, output=20))
    history.message("Go on.")
    session = await a_session(engine, history)
    engine.summarizer.add(a_summary())
    rendered = await engine.windows.render_request(
        engine.ctx, session.id, session.epoch, session.loop_id, KIND
    )
    assert rendered.window.summary_id is not None, "a short history the provider measured long"


async def test_a_compact_control_compacts_before_the_next_request(engine: Engine) -> None:
    history = a_long_history(turns=2, size=100)
    session = await a_session(engine, history)
    quiet = await engine.windows.render_request(
        engine.ctx, session.id, session.epoch, session.loop_id, KIND
    )
    assert quiet.window.summary_id is None
    control = History(session.id).control(ControlCommand.COMPACT)
    await engine.steps.append_inputs(engine.ctx, session.id, [control])
    engine.summarizer.add(a_summary())
    asked = await engine.windows.render_request(
        engine.ctx, session.id, session.epoch, session.loop_id, KIND
    )
    assert asked.window.summary_id is not None
    again = await engine.windows.render_request(
        engine.ctx, session.id, session.epoch, session.loop_id, KIND
    )
    assert again.window.summary_id == asked.window.summary_id, "the control is answered once"


async def test_a_switch_to_a_smaller_window_compacts_first(tmp_path: Path) -> None:
    engine = an_engine(tmp_path, WIDENING)
    history = a_long_history(turns=3)
    last = of_type(history.steps, StepType.MODEL_RESPONSE)[-1]
    history.steps[-3] = Step.model_validate(
        {**last.model_dump(), "header": {"kind": "model_response", "usage": {"input": 1_800}}}
    )
    session = await a_session(engine, history)
    wide = await engine.windows.render_request(
        engine.ctx, session.id, session.epoch, session.loop_id, KIND
    )
    assert wide.window.summary_id is None and wide.window.fill == WIDE.name
    await engine.models.switch_fill(
        engine.ctx, session.id, session.epoch, session.loop_id, MAIN, NARROW, SwitchReason.FALLBACK
    )
    engine.summarizer.add(a_summary())
    narrow = await engine.windows.render_request(
        engine.ctx, session.id, session.epoch, session.loop_id, KIND
    )
    assert narrow.window.fill == NARROW.name and narrow.window.summary_id is not None


# A provider's overflow compacts once and retries once.


async def test_a_providers_overflow_compacts_once_and_retries_once(engine: Engine) -> None:
    session = await a_session(engine, a_long_history(turns=2, size=200))
    rendered = await engine.windows.render_request(
        engine.ctx, session.id, session.epoch, session.loop_id, KIND
    )
    assert rendered.window.summary_id is None
    await record(engine, session, rendered)
    engine.main.add(
        ScriptedFailure(kind=ErrorKind.CONTEXT_OVERFLOW, status=400, message="prompt is too long")
    )
    with pytest.raises(ModelCallFailed) as refused:
        await reply_of(engine.main.stream(rendered.call))
    assert refused.value.kind is ErrorKind.CONTEXT_OVERFLOW
    engine.summarizer.add(a_summary())
    retry = await engine.windows.render_after_overflow(
        engine.ctx, session.id, session.epoch, session.loop_id, KIND, rendered
    )
    assert retry.overflow_retry and retry.window.summary_id is not None
    assert len(rules.prompt_bytes(retry.call, ())) < len(rules.prompt_bytes(rendered.call, ()))
    await record(engine, session, retry)
    engine.main.add(ScriptedFailure(kind=ErrorKind.CONTEXT_OVERFLOW, status=400))
    with pytest.raises(ModelCallFailed):
        await reply_of(engine.main.stream(retry.call))
    before = await history_of(engine, session)
    engine.summarizer.add(a_summary())
    with pytest.raises(ContextOverflow):
        await engine.windows.render_after_overflow(
            engine.ctx, session.id, session.epoch, session.loop_id, KIND, retry
        )
    assert await history_of(engine, session) == before, "a second overflow writes nothing"
    assert engine.summarizer.remaining == 1, "and calls no summarizer"


async def test_a_window_with_nothing_to_fold_overflows_with_nothing_written(
    engine: Engine,
) -> None:
    history = History()
    history.message("Find it." * 2_000)
    session = await a_session(engine, history)
    rendered = await engine.windows.render_request(
        engine.ctx, session.id, session.epoch, session.loop_id, KIND
    )
    assert rendered.window.summary_id is None, "an undelivered input is never folded"
    with pytest.raises(ContextOverflow):
        await engine.windows.render_after_overflow(
            engine.ctx, session.id, session.epoch, session.loop_id, KIND, rendered
        )
    assert len(await history_of(engine, session)) == 1
    assert engine.gate.holds == {} and engine.summarizer.calls == []


async def test_a_side_role_overflow_is_not_compacted(engine: Engine) -> None:
    session = await a_session(engine, a_long_history())
    side = await engine.windows.render_request(
        engine.ctx, session.id, session.epoch, session.loop_id, KIND, "triage"
    )
    with pytest.raises(ContextOverflow):
        await engine.windows.render_after_overflow(
            engine.ctx, session.id, session.epoch, session.loop_id, KIND, side
        )


# A compaction spends only through the gate, and a stale run writes nothing.


async def test_a_cut_summary_is_recorded_and_writes_no_summary(engine: Engine) -> None:
    session = await a_session(engine, a_long_history(turns=6))
    engine.summarizer.add(a_summary(stop_reason=StopReason.OUTPUT_LIMIT, truncated=True))
    with pytest.raises(CompactionFailed, match="not a whole summary"):
        await engine.windows.render_request(
            engine.ctx, session.id, session.epoch, session.loop_id, KIND
        )
    steps = await history_of(engine, session)
    assert of_type(steps, StepType.SUMMARY) == []
    cut = steps[-1]
    assert isinstance(cut.header, ModelResponseHeader) and cut.header.truncated
    assert [billed for _, _, billed in engine.gate.settled] == [True]


async def test_a_summarizer_that_fails_before_it_streams_releases_its_hold(
    engine: Engine,
) -> None:
    session = await a_session(engine, a_long_history())
    engine.summarizer.add(ScriptedFailure(kind=ErrorKind.OVERLOADED, status=529))
    with pytest.raises(ModelCallFailed):
        await engine.windows.render_request(
            engine.ctx, session.id, session.epoch, session.loop_id, KIND
        )
    assert [(usage, billed) for _, usage, billed in engine.gate.settled] == [(None, False)]
    steps = await history_of(engine, session)
    assert steps[-1].type is StepType.MODEL_REQUEST, "persisted before the call, unanswered"


def a_refusal() -> ModelReply:
    return a_summary(text="I can't fold this record.", stop_reason=StopReason.REFUSAL)


async def render_main(engine: Engine, session: Session) -> rules.RenderedRequest:
    return await engine.windows.render_request(
        engine.ctx, session.id, session.epoch, session.loop_id, KIND
    )


async def test_a_refused_summary_is_recorded_and_the_window_is_read_as_it_is(
    engine: Engine,
) -> None:
    session = await a_session(engine, a_long_history(turns=3, size=1_450))
    engine.summarizer.add(a_refusal(), a_summary())
    first = await render_main(engine, session)
    policy = CompactionPolicy()
    assert rules.needs_compaction(first.window, NARROW, policy), "near its limit"
    assert rules.fits(first.window, NARROW), "and still within its model's"
    assert first.window.summary_id is None
    steps = await history_of(engine, session)
    refused = steps[-1]
    assert isinstance(refused.header, ModelResponseHeader) and refused.as_text()
    assert of_type(steps, StepType.SUMMARY) == []
    again = await render_main(engine, session)
    assert again.call == first.call and again.prompt_hash == first.prompt_hash
    assert len(engine.summarizer.calls) == 1, "a failed attempt is not repeated, nor billed"
    assert [billed for _, _, billed in engine.gate.settled] == [True]


async def test_a_failed_attempt_answers_the_compact_control_that_asked_for_it(
    engine: Engine,
) -> None:
    session = await a_session(engine, a_long_history(turns=2, size=100))
    control = History(session.id).control(ControlCommand.COMPACT)
    await engine.steps.append_inputs(engine.ctx, session.id, [control])
    engine.summarizer.add(a_refusal(), a_summary())
    first = await render_main(engine, session)
    assert first.window.summary_id is None
    await render_main(engine, session)
    assert len(engine.summarizer.calls) == 1, "the control was answered"
    asked_again = History(session.id).control(ControlCommand.COMPACT)
    await engine.steps.append_inputs(engine.ctx, session.id, [asked_again])
    compacted = await render_main(engine, session)
    assert compacted.window.summary_id is not None and len(engine.summarizer.calls) == 2


async def test_an_overflow_compacts_after_a_failed_attempt(engine: Engine) -> None:
    session = await a_session(engine, a_long_history(turns=3, size=1_450))
    engine.summarizer.add(a_refusal())
    refused = await render_main(engine, session)
    assert refused.window.summary_id is None
    await record(engine, session, refused)
    engine.summarizer.add(a_summary())
    retry = await engine.windows.render_after_overflow(
        engine.ctx, session.id, session.epoch, session.loop_id, KIND, refused
    )
    assert retry.overflow_retry and retry.window.summary_id is not None


async def test_a_summarizer_call_that_never_left_the_process_releases_its_hold(
    engine: Engine,
) -> None:
    session = await a_session(engine, a_long_history())
    engine.summarizer.add(ScriptedFailure(kind=ErrorKind.CREDENTIAL, message="no key"))
    with pytest.raises(ModelCallFailed):
        await render_main(engine, session)
    assert [(usage, billed) for _, usage, billed in engine.gate.settled] == [(None, False)]


async def test_a_summary_whose_stream_broke_is_billed_and_recorded_cut(engine: Engine) -> None:
    session = await a_session(engine, a_long_history())
    arrived = a_summary(text="The grip", stop_reason=None, truncated=True)
    engine.summarizer.add(ScriptedFailure(kind=ErrorKind.TRANSIENT, partial=arrived))
    with pytest.raises(ModelCallFailed):
        await render_main(engine, session)
    assert [(usage, billed) for _, usage, billed in engine.gate.settled] == [(None, True)]
    cut = (await history_of(engine, session))[-1]
    assert isinstance(cut.header, ModelResponseHeader) and cut.header.truncated
    assert cut.as_text() == "The grip"


async def test_a_refused_gate_calls_no_model_and_writes_nothing(engine: Engine) -> None:
    session = await a_session(engine, a_long_history())
    before = await history_of(engine, session)
    engine.gate.refusing = True
    engine.summarizer.add(a_summary())
    with pytest.raises(Unavailable):
        await engine.windows.render_request(
            engine.ctx, session.id, session.epoch, session.loop_id, KIND
        )
    assert await history_of(engine, session) == before
    assert engine.summarizer.calls == []


async def test_a_run_that_lost_its_claim_compacts_nothing_and_spends_nothing(
    engine: Engine,
) -> None:
    session = await a_session(engine, a_long_history())
    before = await history_of(engine, session)
    await engine.steps.begin_run(engine.ctx, session.id)
    engine.summarizer.add(a_summary())
    with pytest.raises(StaleWriter):
        await engine.windows.render_request(
            engine.ctx, session.id, session.epoch, session.loop_id, KIND
        )
    assert await history_of(engine, session) == before
    assert engine.summarizer.calls == []
    assert [billed for _, _, billed in engine.gate.settled] == [False]


async def test_a_main_request_over_an_open_tool_call_is_refused(engine: Engine) -> None:
    history = History()
    objective = history.message("Find it.")
    reply = history.response(history.request((objective,)), "", [("c1", "read_log", {})])
    history.call(reply, "c1")
    session = await a_session(engine, history)
    with pytest.raises(PreconditionFailed):
        await engine.windows.render_request(
            engine.ctx, session.id, session.epoch, session.loop_id, KIND
        )


async def test_a_side_role_reads_a_suffix_sized_to_its_own_fill(engine: Engine) -> None:
    session = await a_session(engine, a_long_history())
    side = await engine.windows.render_request(
        engine.ctx, session.id, session.epoch, session.loop_id, KIND, "triage"
    )
    assert (side.window.role, side.window.fill) == ("triage", TRIAGE.name)
    assert side.window.left_edge > 1 and side.window.summary_id is None
    assert side.window.used_tokens <= rules.limit_tokens(TRIAGE, CompactionPolicy())
    assert side.delivers == () and engine.summarizer.calls == []
    assert of_type(await history_of(engine, session), StepType.SUMMARY) == []


# Replay: a recorded request re-renders to its prompt's hash.


async def test_re_rendering_each_recorded_request_reproduces_its_prompt_hash(
    tmp_path: Path,
) -> None:
    engine = an_engine(tmp_path, WIDENING)
    history = History()
    history.message("Find why the robot drops the object.")
    session = await a_session(engine, history)
    for n in range(3):
        rendered = await engine.windows.render_request(
            engine.ctx, session.id, session.epoch, session.loop_id, KIND, plan=f"step {n}"
        )
        request = await record(engine, session, rendered)
        engine.main.add(
            ModelReply(
                blocks=(TextBlock(text=f"Reading part {n}."),),
                stop_reason=StopReason.END_TURN,
                usage=Usage(input=300 * (n + 1), output=20),
                model=WIDE.model,
            )
        )
        reply = await reply_of(engine.main.stream(rendered.call))
        turn = History(session.id)
        turn.loop_id = session.loop_id
        response = turn.response(
            request,
            f"Reading part {n}.",
            [(f"c{n}", "read_log", {"n": n, "from": 1})],
            usage=reply.usage,
        )
        turn.result(turn.call(response, f"c{n}"), f"part {n} of the log")
        turn.message(f"And part {n + 1}?")
        await engine.steps.append_steps(engine.ctx, session.id, session.epoch, turn.steps)
    steps = await history_of(engine, session)
    recorded = [s for s in steps if s.type is StepType.MODEL_REQUEST]
    assert len(recorded) == 3
    hashes: list[str] = []
    values: list[bytes] = []
    for n, request in enumerate(recorded):
        prefix = [s for s in steps if s.seq < request.seq]
        draft = rules.render_main(prefix, KIND, WIDE, 1, f"step {n}", CompactionPolicy())
        values.append(rules.prompt_bytes(draft.call, draft.attachments))
        hashes.append(await engine.hashes.keyed_hash(engine.ctx, session.id, values[-1]))
        assert isinstance(request.header, ModelRequestHeader)
        assert hashes[-1] == request.header.prompt_hash
        assert request.refs == draft.delivers
    elsewhere = await engine.hashes.keyed_hash(engine.ctx, new_id(), values[-1])
    assert elsewhere != hashes[-1], "the hash is keyed by its session"


# A tool result over the bound is stored as an artifact.


def a_large_result(session_id: UUID, size: int = 30_000) -> tuple[History, Step, str]:
    history = History(session_id)
    objective = history.message("Read the whole log.")
    reply = history.response(history.request((objective,)), "", [("c1", "read_log", {})])
    text = "".join(f"{n:07d}\n" for n in range(size // 8))
    result = history.result(history.call(reply, "c1"), text)
    return history, result, text


async def test_a_result_over_the_bound_is_kept_as_an_artifact(engine: Engine) -> None:
    history, result, text = a_large_result(new_id())
    session = await a_session(engine, History(history.session_id))
    bounded = await engine.windows.bound_tool_response(engine.ctx, session.id, result)
    header = bounded.header
    assert isinstance(header, ToolResponseHeader) and header.artifact is not None
    assert header.artifact.characters == len(text)
    head, tail = bounded.as_tool_response().parts
    assert head == TextBlock(text=text[:2_000]) and tail == TextBlock(text=text[-2_000:])
    assert (bounded.id, bounded.responds_to) == (result.id, result.responds_to)
    again = await engine.windows.bound_tool_response(engine.ctx, session.id, result)
    assert again == bounded, "the same response keeps the same artifact"
    pages, offset = [], 0
    while True:
        page = await engine.windows.get_artifact(
            engine.ctx, session.id, header.artifact.id, offset, 100_000
        )
        pages.append(page.text)
        offset += len(page.text)
        if not page.has_more:
            break
    assert "".join(pages) == text and len(pages) == 2, "a page is at most the policy's"
    history.steps[-1] = bounded
    await engine.steps.append_steps(engine.ctx, session.id, session.epoch, history.steps)
    draft = rules.render_main(
        await history_of(engine, session), KIND, NARROW, 1, None, CompactionPolicy()
    )
    shown = next(b for m in draft.call.messages for b in m.blocks if isinstance(b, ToolResultBlock))
    first, notice, last = shown.parts
    assert (first, last) == (head, tail)
    assert isinstance(notice, TextBlock) and str(header.artifact.id) in notice.text
    assert f"{len(text) - 4_000} characters" in notice.text


async def test_a_result_within_the_bound_is_kept_as_it_is(engine: Engine) -> None:
    history, result, _ = a_large_result(new_id(), size=20_000)
    session = await a_session(engine, History(history.session_id))
    assert await engine.windows.bound_tool_response(engine.ctx, session.id, result) is result
    with pytest.raises(ValidationFailed):
        await engine.windows.bound_tool_response(engine.ctx, session.id, history.steps[0])
    with pytest.raises(NotFound):
        await engine.windows.get_artifact(engine.ctx, session.id, new_id(), 0, 10)


async def test_another_tenant_reads_no_artifact(engine: Engine) -> None:
    history, result, _ = a_large_result(new_id())
    session = await a_session(engine, History(history.session_id))
    bounded = await engine.windows.bound_tool_response(engine.ctx, session.id, result)
    assert isinstance(bounded.header, ToolResponseHeader) and bounded.header.artifact is not None
    with pytest.raises(NotFound):
        await engine.windows.get_artifact(
            context(Role.MEMBER), session.id, bounded.header.artifact.id, 0, 10
        )


def test_a_step_that_names_an_artifact_holds_its_head_and_tail() -> None:
    _, result, _ = a_large_result(new_id())
    handle = ArtifactRef(id=new_id(), characters=30_000)
    whole = {**result.model_dump(), "header": {"kind": "tool_response", "artifact": handle}}
    with pytest.raises(ValueError, match="head and its tail"):
        Step.model_validate(whole)
    content = Content(
        blocks=(
            ToolResultBlock(
                tool_use_id="c1", parts=(TextBlock(text="head"), TextBlock(text="tail"))
            ),
        )
    )
    assert Step.model_validate({**whole, "content": content}).created_at == AT


# The root's own wiring.


async def authorized(managers: Managers, ctx: TenantContext, session_id: UUID) -> None:
    """A session of the caller's, with its authority: a render reads who
    spoke and who pays from it."""
    now = utcnow()
    made = AgentSession(
        id=session_id,
        created_at=now,
        updated_at=now,
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
        title="a render",
        kind="investigator",
        kind_version=1,
        root_id=session_id,
    )
    await managers.agent_sessions.create_session(ctx, made)
    await managers.attribution.open_authority(ctx, session_id, AuthorityMode.STEADY)


async def test_a_root_hashes_a_prompt_under_the_sessions_key(tmp_path: Path) -> None:
    managers = build_managers(StorageMemoryImpl(), InfraLocalImpl(tmp_path), model_prices=Priced())
    ctx = context(Role.MEMBER)
    history = History()
    await authorized(managers, ctx, history.session_id)
    history.message("Find it.")
    await managers.steps.append_inputs(ctx, history.session_id, history.steps)
    epoch = await managers.steps.begin_run(ctx, history.session_id)
    await managers.models.resolve_fill_set(
        ctx, history.session_id, [MAIN, SUMMARIZER], Eligibility()
    )
    first, again = [
        await managers.windows.render_request(
            ctx, history.session_id, epoch, history.steps[0].id, KIND
        )
        for _ in range(2)
    ]
    assert first.prompt_hash == again.prompt_hash, "the same steps, the same hash"
    other = History()
    await authorized(managers, ctx, other.session_id)
    other.message("Find it.")
    await managers.steps.append_inputs(ctx, other.session_id, other.steps)
    epoch = await managers.steps.begin_run(ctx, other.session_id)
    await managers.models.resolve_fill_set(ctx, other.session_id, [MAIN, SUMMARIZER], Eligibility())
    elsewhere = await managers.windows.render_request(
        ctx, other.session_id, epoch, other.steps[0].id, KIND
    )
    assert elsewhere.call == first.call and elsewhere.prompt_hash != first.prompt_hash, (
        "keyed by the session: one prompt in two sessions hashes apart"
    )


async def test_a_root_with_the_nulls_wired_refuses_to_hash_or_spend(tmp_path: Path) -> None:
    managers = build_managers(
        StorageMemoryImpl(),
        InfraLocalImpl(tmp_path),
        model_prices=Priced(),
        call_gate=CallGateNullImpl(),
        prompt_hash=PromptHashNullImpl(),
    )
    ctx = context(Role.MEMBER)
    history = History()
    await authorized(managers, ctx, history.session_id)
    history.message("Find it.")
    await managers.steps.append_inputs(ctx, history.session_id, history.steps)
    epoch = await managers.steps.begin_run(ctx, history.session_id)
    await managers.models.resolve_fill_set(
        ctx, history.session_id, [MAIN, SUMMARIZER], Eligibility()
    )
    with pytest.raises(Unavailable, match="key service"):
        await managers.windows.render_request(
            ctx, history.session_id, epoch, history.steps[0].id, KIND
        )
    call = ModelCall(model=SUMMARY_FILL.model, messages=(), max_output_tokens=1)
    with pytest.raises(Unavailable, match="budget gate"):
        await CallGateNullImpl().authorize(
            ctx, history.session_id, PAYER, SUMMARIZER, SUMMARY_FILL, call
        )
