"""A request's prompt bytes, made once: a long scripted replay through the
windows, where every request drafted keeps the bytes, the prompt's hash,
the window's used tokens, and the gate's prompt size that the reference
below gives. The reference is the prompt's bytes as one `json.dumps` of the
whole payload, and each size read from bytes made afresh for it."""

import gc
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from unit.test_windows import (
    SUMMARY_FILL,
    TRIAGE,
    WIDE,
    WIDENING,
    Engine,
    Session,
    a_summary,
    an_agent_session,
    an_engine,
    record,
)

from acme.integrations.model_providers.calls import ModelCall, ToolSpec
from acme.integrations.model_providers.types import Usage
from acme.om.agent_steps.types.agent_step import Actor, AgentStep, AgentStepType, Origin
from acme.om.agent_steps.types.content import (
    Attachment,
    Children,
    Content,
    ImageBlock,
    TextBlock,
)
from acme.om.agent_steps.types.header import (
    InputHeader,
    ModelRequestHeader,
    ModelResponseHeader,
)
from acme.om.base import new_id
from acme.om.language_models.types.fill import MAIN, SUMMARIZER, Fill
from acme.om.spend_budgets.types.exposure import CacheWrite, CallShape, PromptCount, PromptSize
from acme.om.windows import rules
from acme.om.windows.types.kind import KindPrompts
from acme.om.windows.types.policy import CompactionPolicy
from acme.om.windows.types.window import RenderedRequest

from contracts.histories import History

TURNS = 160
POLICY = CompactionPolicy()
FILLS = {fill.name: fill for fill in (WIDE, SUMMARY_FILL, TRIAGE)}
KIND = KindPrompts(
    kind="investigator",
    version=1,
    prompts=("You investigate faults.", "Answer in the reader's language: ¿qué pasó?"),
    standing="Keep the order's trail — every hop, every 🙂 a person left.",
    tools=(
        ToolSpec(
            name="read_log",
            description="Reads a log, from a line.",
            input_schema={"type": "object", "properties": {"n": {"type": "integer"}}},
        ),
        ToolSpec(
            name="measure",
            description="Measures a latency.",
            input_schema={"required": ["at"], "type": "object", "minimum": 1e-05},
        ),
    ),
)
SAID = (
    "The order drops at 4.2 s.",
    "Ça casse après le paiement — voir le journal.",
    'A path C:\\shop\\orders and a "quoted" word.',
    "A colour code \x1b[31mred\x1b[0m, a tab\tand a bell\x07.",
    "An emoji 🙂 and a rubout \x7f in one line.",
)


def reference_bytes(call: ModelCall, attachments: Sequence[Attachment]) -> bytes:
    """The prompt's bytes as their definition reads: the call and the
    placeholders, one payload, every key in order."""
    payload = {
        "call": call.model_dump(mode="json", exclude={"files"}),
        "attachments": [attachment.model_dump(mode="json") for attachment in attachments],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def reference_shape(call: ModelCall, fill: Fill) -> CallShape:
    """What the gate holds of a call, read from bytes made afresh for it."""
    size = len(reference_bytes(call, ())) + sum(len(file.data) for file in call.files)
    marked = (
        call.system_cache
        or call.system_break is not None
        or any(message.cache for message in call.messages)
    )
    return CallShape(
        prompt=PromptSize(tokens=size, counted_by=PromptCount.UPPER_BOUND),
        cache=CacheWrite.SHORT if marked else CacheWrite.NONE,
        output_bound=fill.max_output_tokens,
    )


def reference_used(
    steps: Sequence[AgentStep], fill: Fill, plan: str | None, draft: rules.Draft
) -> tuple[int, bool]:
    """A main window's used tokens: the latest report of its fill over the
    same window plus an estimate for the steps since, else the whole
    request's bytes, made afresh; and whether they were the bytes."""
    ex = rules.exchanges(steps)
    summary = rules.latest_summary(steps)
    start = rules.window_start(steps, summary)
    summary_id = None if summary is None else summary.id
    for index in range(len(steps) - 1, start - 1, -1):
        response = steps[index]
        if not ex.is_response(response) or response.responds_to is None:
            continue
        request = ex.by_id[response.responds_to].header
        header = response.header
        usage = header.usage if isinstance(header, ModelResponseHeader) else None
        assert isinstance(request, ModelRequestHeader)
        if usage is None or request.fill != fill.name or request.summary_id != summary_id:
            continue
        since = sum(rules.step_chars(s, POLICY) for s in steps[index + 1 :] if s.id in ex.rendered)
        reported = usage.prompt + usage.output + usage.thinking
        return reported + rules.tokens(since + len(plan or ""), POLICY), False
    return rules.tokens(len(reference_bytes(draft.call, draft.attachments)), POLICY), True


class Drafts:
    """Every draft the windows make, and for each main one what it read."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.made: list[rules.Draft] = []
        self.main: dict[int, tuple[list[AgentStep], Fill, str | None]] = {}
        made = self.made

        @dataclass(frozen=True)
        class Recorded(rules.Draft):
            def __post_init__(self) -> None:
                made.append(self)

        render_main = rules.render_main

        def main(
            steps: Sequence[AgentStep],
            kind: KindPrompts,
            fill: Fill,
            fill_set_version: int,
            plan: str | None,
            policy: CompactionPolicy,
        ) -> rules.Draft:
            draft = render_main(steps, kind, fill, fill_set_version, plan, policy)
            self.main[id(draft)] = (list(steps), fill, plan)
            return draft

        monkeypatch.setattr(rules, "Draft", Recorded)
        monkeypatch.setattr(rules, "render_main", main)


async def history(engine: Engine, session: Session) -> list[AgentStep]:
    steps: list[AgentStep] = []
    while True:
        after = steps[-1].seq if steps else 0
        page = await engine.steps.get_agent_steps(engine.ctx, session.id, after, 200)
        steps.extend(page.items)
        if not page.has_more or not page.items:
            return steps


def an_input(turn: History, n: int) -> AgentStep:
    """A person's message, every few turns with an image whose name is not
    plain ASCII, so the hash's bytes and the gate's differ by its
    placeholder."""
    text = f"{SAID[n % len(SAID)]} (turn {n})"
    if n % 3:
        return turn.message(text)
    plot = Attachment(
        id=new_id(), name=f"latência-{n}.png", media_type="image/png", size=9, hash=f"k:{n}"
    )
    return turn.add(
        type=AgentStepType.MESSAGE,
        actor=Actor.PERSON,
        origin=Origin.PORTAL,
        header=InputHeader(principal=turn.person),
        content=Content(blocks=(TextBlock(text=text), ImageBlock(attachment_id=plot.id))),
        children=Children(attachments=(plot,)),
    )


async def answered(
    engine: Engine, session: Session, rendered: RenderedRequest, n: int
) -> list[AgentStep]:
    """The request recorded, the model's reply to it, and a result for each
    tool it called: some short, one in seven over the bound and kept as a
    tool output. A stretch of replies reports no usage, so the window is
    sized from the request's bytes."""
    request = await record(engine, session, rendered)
    text = f"{SAID[(n + 1) % len(SAID)]} Reading part {n}."
    uses: list[tuple[str, str, dict[str, Any]]] = [
        (f"c{n}", "read_log", {"n": n, "from": 1, "filter": {"z": "é", "a": [1.5, 1e16]}})
    ]
    if n % 4 == 0:
        uses.append((f"m{n}", "measure", {"at": 0.1 * n, "unit": "s", "tag": "μs\\n"}))
    turn = History(session.id)
    turn.loop_id = session.loop_id
    quiet = n < 12 or 70 <= n < 80
    response = turn.response(
        request,
        text,
        uses,
        thinking=f"Weighing part {n} — {'ñ' * (n % 5)}" if n % 2 else None,
        usage=None if quiet else Usage(input=rendered.window.used_tokens, output=40),
    )
    for index, (use_id, tool, _) in enumerate(uses):
        size = 30_000 if (n + index) % 7 == 0 else 600 + 37 * n
        said = (f"{n}:{index} " + SAID[(n + index) % len(SAID)]) * (size // 40 + 1)
        result = turn.result(turn.call(response, use_id, tool), said[:size])
        turn.steps[-1] = await engine.windows.bound_tool_response(engine.ctx, session.id, result)
    if n % 2 == 0:
        an_input(turn, n)
    if n % 5 == 0:
        turn.event(f"The checkout redeployed — build {n}.")
    return turn.steps


async def test_every_request_a_long_replay_drafts_keeps_its_bytes_hash_size_and_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    drafts = Drafts(monkeypatch)
    engine = an_engine(tmp_path, WIDENING)
    opening = History()
    opening.message("Find why the checkout drops the order, and fix it — vite.")
    session = await an_agent_session(engine, opening)
    sent: list[RenderedRequest] = []
    overflowed = False

    def stocked() -> None:
        if engine.summarizer.remaining == 0:
            engine.summarizer.add(a_summary(f"The order drops — fold {len(drafts.made)}."))

    for n in range(TURNS):
        if n % 9 == 4:
            side = await engine.windows.render_request(
                engine.ctx, session.id, session.epoch, session.loop_id, KIND, "triage"
            )
            sent.append(side)
        stocked()
        before = sum(1 for d in drafts.made if d.window.role == SUMMARIZER)
        rendered = await engine.windows.render_request(
            engine.ctx,
            session.id,
            session.epoch,
            session.loop_id,
            KIND,
            plan=f"Step {n}: ¿y ahora?",
        )
        folds = sum(1 for d in drafts.made if d.window.role == SUMMARIZER)
        if n >= 100 and not overflowed and folds == before:
            stocked()
            rendered = await engine.windows.render_after_overflow(
                engine.ctx, session.id, session.epoch, session.loop_id, KIND, rendered
            )
            assert rendered.overflow_retry
            overflowed = True
        sent.append(rendered)
        await engine.steps.append_agent_steps(
            engine.ctx, session.id, session.epoch, await answered(engine, session, rendered, n)
        )
    assert overflowed

    steps = await history(engine, session)
    folded = [d for d in drafts.made if d.window.role == SUMMARIZER]
    summarized = [
        s for s in steps if isinstance(s.header, ModelRequestHeader) and s.header.role == SUMMARIZER
    ]
    assert len(folded) == len(summarized) >= 10, "the replay compacts again and again"
    assert sum(1 for s in steps if s.type is AgentStepType.SUMMARY) == len(summarized)
    assert sum(1 for d in drafts.made if d.attachments) > 100, "an image's placeholder"
    after_folds = [
        read for read, _, _ in drafts.main.values() if read[-1].type is AgentStepType.SUMMARY
    ]
    assert len(after_folds) >= 10, "a main draft over each new summary"
    sized_afresh = 0
    for draft in drafts.made:
        role = draft.window.role
        assert draft.prompt == reference_bytes(draft.call, draft.attachments)
        if role == MAIN:
            steps_read, fill, plan = drafts.main[id(draft)]
            used, from_bytes = reference_used(steps_read, fill, plan, draft)
            sized_afresh += from_bytes
        else:
            used = rules.tokens(len(reference_bytes(draft.call, draft.attachments)), POLICY)
        assert draft.window.used_tokens == used, f"a {role} window's used tokens"
        fill = FILLS[draft.window.fill]
        shape = reference_shape(draft.call, fill)
        assert rules.call_shape(draft.call, fill) == shape, "the size its render recorded"
        assert rules.call_shape(draft.call.model_copy(), fill) == shape, "the size made afresh"
    assert sized_afresh >= 30, "many a main window is sized from its bytes alone"
    assert {r.window.role for r in sent} == {MAIN, "triage"}
    for rendered in sent:
        value = reference_bytes(rendered.call, rendered.attachments)
        assert rendered.prompt_hash == await engine.hashes.keyed_hash(engine.ctx, session.id, value)
        fill = FILLS[rendered.window.fill]
        assert rules.call_shape(rendered.call, fill) == reference_shape(rendered.call, fill)
    for draft, request in zip(folded, summarized, strict=True):
        assert isinstance(request.header, ModelRequestHeader)
        value = reference_bytes(draft.call, ())
        assert request.header.prompt_hash == await engine.hashes.keyed_hash(
            engine.ctx, session.id, value
        )


def a_draft() -> rules.Draft:
    history = History()
    history.message("Find why the checkout drops the order — vite.")
    return rules.render_main(history.steps, KIND, WIDE, 1, "Read the log.", POLICY)


def test_the_gate_reads_the_size_its_render_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    draft = a_draft()
    shape = reference_shape(draft.call, WIDE)
    made: Callable[[ModelCall, Sequence[Attachment]], bytes] = rules.prompt_bytes

    def never(call: ModelCall, attachments: Sequence[Attachment]) -> bytes:
        raise AssertionError("the gate made the prompt's bytes again")

    monkeypatch.setattr(rules, "prompt_bytes", never)
    assert rules.call_shape(draft.call, WIDE) == shape
    monkeypatch.setattr(rules, "prompt_bytes", made)
    assert rules.call_shape(draft.call.model_copy(), WIDE) == shape, "a copy is sized afresh"


def test_a_recorded_size_goes_with_its_call() -> None:
    call = a_draft().call.model_copy()
    rules.prompt_bytes(call, ())
    key = id(call)
    assert rules._sizes.of(call) == len(reference_bytes(call, ()))
    del call
    gc.collect()
    assert key not in rules._sizes._held
