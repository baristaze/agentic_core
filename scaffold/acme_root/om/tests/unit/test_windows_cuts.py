"""No window cut separates a tool request from its response, nor a message
from what answers it: a property held over many generated histories, with
steering inputs landing mid-call, replies cut short or abandoned, and calls
answered out of order. Every window the rules choose, a main window after a
summary at any cut, a side role's suffix of any size, and a compaction's
fold, renders a request the provider takes: each tool use answered in the
next turn, and each answer after its use."""

import random
from collections.abc import Sequence

import pytest
from contracts.histories import History

from acme.integrations.model_providers.calls import ModelCall, ToolSpec
from acme.integrations.model_providers.types import ProviderName
from acme.om.models.types.fill import Fill
from acme.om.steps.types.content import ToolResultBlock, ToolUseBlock
from acme.om.steps.types.header import ToolRequestHeader
from acme.om.steps.types.step import Step, StepType
from acme.om.windows import rules
from acme.om.windows.types.kind import KindPrompts
from acme.om.windows.types.policy import CompactionPolicy

KIND = KindPrompts(kind="k", version=1, prompts=("Work.",), tools=(ToolSpec(name="read_log"),))
POLICY = CompactionPolicy(chars_per_token=1.0, step_overhead=8)
SEEDS = range(300)


def fill(window: int) -> Fill:
    return Fill(
        provider=ProviderName.ANTHROPIC,
        model="claude-sonnet-5-5",
        max_output_tokens=50,
        context_window=window,
    )


def generated(seed: int) -> History:
    """A history a loop could write: inputs arrive any time, a request
    delivers what is pending, its reply may be cut or abandoned (and then
    delivers nothing), and a reply's tool calls are answered in any order,
    with inputs landing between them."""
    rng = random.Random(seed)
    history = History()
    pending = [history.message("the objective")]
    for _ in range(rng.randint(1, 9)):
        if rng.random() < 0.3:
            pending.append(history.event(f"an event {len(history.steps)}"))
        request = history.request(pending)
        fate = rng.random()
        if fate < 0.1:
            history.response(request, "cut", truncated=True)
            continue
        if fate < 0.15:
            history.response(request, abandoned=True)
            continue
        pending = []
        uses = [
            (f"u{len(history.steps)}_{n}", "read_log", {"n": n}) for n in range(rng.randint(0, 3))
        ]
        said = "said" if rng.random() < 0.7 or not uses else ""
        response = history.response(
            request, said, uses, thinking="why" if rng.random() < 0.3 else None
        )
        calls = [history.call(response, use_id) for use_id, _, _ in uses]
        rng.shuffle(calls)
        for call in calls:
            if rng.random() < 0.3:
                pending.append(history.message(f"a steer {len(history.steps)}"))
            if rng.random() < 0.2:
                history.changed("a person moved the arm")
            history.result(call, "r" * rng.randint(1, 300))
    return history


def pairs(steps: Sequence[Step]) -> list[tuple[int, int]]:
    """Every pair a window must keep together, as indexes, earlier first: a
    response and its request, a tool request and the response that asked
    for it, a tool response and its request, a request and each input it
    delivered."""
    at = {step.id: index for index, step in enumerate(steps)}
    found: list[tuple[int, int]] = []
    for index, step in enumerate(steps):
        if step.responds_to is not None:
            found.append((at[step.responds_to], index))
        if step.type in (StepType.TOOL_REQUEST, StepType.MODEL_REQUEST):
            found.extend((at[ref], index) for ref in step.refs)
    return found


def assert_taken(call: ModelCall) -> None:
    """What a provider refuses, held off: the conversation opens on a
    person's turn, each tool use is answered in the next turn and first in
    it, and each answer follows its use."""
    messages = call.messages
    assert messages and messages[0].role == "user"
    for index, message in enumerate(messages):
        uses = [b.id for b in message.blocks if isinstance(b, ToolUseBlock)]
        answers = [b.tool_use_id for b in message.blocks if isinstance(b, ToolResultBlock)]
        if message.role == "assistant":
            assert not answers
            if uses:
                after = messages[index + 1]
                got = [b.tool_use_id for b in after.blocks if isinstance(b, ToolResultBlock)]
                assert after.role == "user" and sorted(got) == sorted(uses)
                assert all(isinstance(b, ToolResultBlock) for b in after.blocks[: len(got)])
        else:
            assert not uses
            if answers:
                before = messages[index - 1]
                assert before.role == "assistant"
                assert sorted(answers) == sorted(
                    b.id for b in before.blocks if isinstance(b, ToolUseBlock)
                )


@pytest.mark.parametrize("seed", SEEDS)
def test_no_consistent_cut_separates_a_pair(seed: int) -> None:
    steps = generated(seed).steps
    cuts = rules.consistent_cuts(steps)
    assert {0, len(steps)} <= cuts
    for earlier, later in pairs(steps):
        assert not any(earlier < cut <= later for cut in cuts), (earlier, later)


@pytest.mark.parametrize("seed", SEEDS)
def test_a_main_window_after_a_summary_at_any_cut_renders_a_request_the_provider_takes(
    seed: int,
) -> None:
    steps = generated(seed).steps
    assert_taken(rules.render_main(steps, KIND, fill(100_000), 1, "plan", POLICY).call)
    for cut in sorted(rules.consistent_cuts(steps) - {0}):
        history = History(steps[0].session_id)
        history.steps = list(steps)
        summary = history.summarize(steps[cut - 1].seq, "folded")
        draft = rules.render_main(history.steps, KIND, fill(100_000), 1, None, POLICY)
        assert_taken(draft.call)
        assert draft.window.summary_id == summary.id
        first_kept = steps[cut].seq if cut < len(steps) else steps[-1].seq + 1
        assert draft.window.left_edge == first_kept


@pytest.mark.parametrize("seed", SEEDS)
def test_a_side_roles_suffix_of_any_size_is_consistent(seed: int) -> None:
    history = generated(seed)
    request = history.request()
    open_reply = history.response(request, "", [("open_1", "read_log", {})])
    history.call(open_reply, "open_1")
    steps = history.steps
    cuts = rules.consistent_cuts(steps)
    for window in (200, 600, 1_500, 4_000, 100_000):
        draft = rules.render_side(steps, KIND, "triage", fill(window), 1, POLICY)
        assert_taken(draft.call)
        start = next(i for i, s in enumerate(steps) if s.seq == draft.window.left_edge)
        assert start in cuts
        assert draft.window.right_edge < open_reply.seq, "it ends before the open call"
        assert draft.delivers == ()


@pytest.mark.parametrize("seed", SEEDS)
def test_a_fold_cuts_on_a_whole_exchange_and_never_folds_an_undelivered_input(seed: int) -> None:
    history = generated(seed)
    pending = history.message("a last word")
    steps = history.steps
    ex = rules.exchanges(steps)
    cut = rules.fold_cut(steps, fill(400), fill(2_000), POLICY)
    if cut is None:
        return
    assert cut in rules.consistent_cuts(steps)
    undelivered = [
        i
        for i, s in enumerate(steps)
        if s.id == pending.id or (s.type in rules.DELIVERABLE and s.id not in ex.delivered)
    ]
    assert cut <= undelivered[0]
    latest = max(i for i, s in enumerate(steps) if s.id in ex.responses)
    assert cut <= latest, "the latest exchange, unread, stays verbatim"
    assert any(s.id in ex.rendered for s in steps[:cut]), "a fold folds something"


def test_a_tool_request_is_never_cut_from_its_response() -> None:
    history = History()
    objective = history.message("go")
    reply = history.response(history.request((objective,)), "", [("c1", "read_log", {})])
    call = history.call(reply, "c1")
    history.result(call, "done")
    cuts = rules.consistent_cuts(history.steps)
    assert cuts == {0, len(history.steps)}
    assert isinstance(call.header, ToolRequestHeader)
