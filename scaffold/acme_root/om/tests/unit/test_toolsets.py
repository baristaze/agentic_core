"""Toolsets: a kind built from toolsets renders the bytes the same tools
listed alone do; a deferred toolset is one line in `load_toolset`'s
description until a load adds its tools, after every tool before them,
recorded as a `switched` step that a replay of the history renders the
same way; nothing else changes a session's tool definitions; and a call
of a tool its own turn loads is refused as not offered, after a lost run
as well, and the session goes on."""

import json
from collections.abc import Sequence
from itertools import pairwise
from pathlib import Path
from uuid import UUID

import pytest
from contracts.loops import ALLOWED, Loop, call, loop_over, reply, said, tools, use

from acme.integrations.model_providers.anthropic import request_body
from acme.integrations.model_providers.calls import ModelCall
from acme.om.agents.loop_rules import kind_prompts
from acme.om.agents.types.kind import AgentKind, AgentKindCatalog, DoneRule
from acme.om.agents.types.run import RunEnd
from acme.om.attribution.types.authority import AuthorityMode
from acme.om.context import TenantContext
from acme.om.models.types.fill import MAIN
from acme.om.steps.types.content import TextBlock
from acme.om.steps.types.header import (
    LoopOutcome,
    ModelRequestHeader,
    SwitchedHeader,
    ToolFailure,
    ToolRequestHeader,
    ToolResponseHeader,
    ToolsetSwitch,
)
from acme.om.steps.types.step import Step, StepType
from acme.om.tools.native.load_toolset import LoadToolsetToolImpl, callable_tools, loads
from acme.om.tools.native.write_plan import current_plan
from acme.om.tools.registry import ToolRegistry
from acme.om.tools.types.toolset import LOAD_TOOLSET, Toolset
from acme.om.windows.rules import render_main
from acme.om.windows.types.policy import CompactionPolicy

# `slow` sorts after the tools a load of `writes` adds, so a load that
# sorted them in among the rest would move it.
READS = Toolset(name="reads", version=1, summary="Look the records up.", tools=("lookup", "slow"))
WRITES = Toolset(
    name="writes", version=1, summary="Write a note, and send it out.", tools=("send", "note")
)


def operator(**shape: object) -> AgentKind:
    """The operator kind at version 1, its tools as `shape` names them."""
    return AgentKind.model_validate(
        {
            "name": "operator",
            "version": 1,
            "done_rule": DoneRule.ANSWER,
            "authority": AuthorityMode.DELEGATED,
            "prompts": ("You keep the records, and act on them when asked.",),
            "policy": ALLOWED,
            **shape,
        }
    )


FLAT = operator(tools=("lookup", "note", "send"))
BUILT = operator(tools=("lookup",), toolsets=(WRITES,))
DEFERS = operator(toolsets=(READS,), deferred=(WRITES,))


def names(call_: ModelCall) -> list[str]:
    return [tool.name for tool in call_.tools]


def failure_of(step: Step) -> ToolFailure | None:
    header = step.header
    assert isinstance(header, ToolResponseHeader)
    return header.failure


async def ran(loop: Loop, session_id: UUID) -> list[Step]:
    run = await loop.loops.run(loop.owner, session_id)
    assert (run.end, run.outcome) == (RunEnd.ENDED, LoopOutcome.SUCCEEDED)
    return await loop.history(session_id)


async def replayed(loop: Loop, kind: AgentKind, session_id: UUID) -> list[ModelCall]:
    """Each main request of the history rendered again from the steps before
    it, its registry and its loads read off those steps as a run reads
    them."""
    steps = await loop.history(session_id)
    session = await loop.managers.agent_sessions.get_session(loop.owner, session_id)
    fill_set = await loop.managers.models.get_fill_set(loop.owner, session_id)
    fill = fill_set.fill_for(MAIN)
    assert fill is not None
    catalog = (LoadToolsetToolImpl(), *loop.tools.values())
    calls: list[ModelCall] = []
    for index, step in enumerate(steps):
        header = step.header
        if not isinstance(header, ModelRequestHeader) or header.role != MAIN:
            continue
        before = steps[:index]
        held = callable_tools(session.tools, kind.waiting, loads(before))
        registry = ToolRegistry(tool for tool in catalog if tool.spec.name in held)
        prompts = kind_prompts(kind, registry, loads(before))
        kept = current_plan(before)
        plan = None if kept is None else kept.text
        drafted = render_main(
            before, prompts, fill, header.fill_set_version, plan, CompactionPolicy()
        )
        calls.append(drafted.call)
    return calls


# The kind and its catalog.


def test_a_kinds_toolsets_and_deferred_toolsets_join_its_tools_once_and_refold_alike() -> None:
    assert set(BUILT.tools) == set(FLAT.tools)
    assert set(DEFERS.tools) == {"lookup", "slow", "note", "send", LOAD_TOOLSET}
    assert DEFERS.waiting == frozenset({"note", "send"})
    again = AgentKind.model_validate(DEFERS.model_dump())
    assert again == DEFERS, "a folded kind folds to itself"
    holds_both = operator(
        toolsets=(READS, WRITES), deferred=(WRITES.model_copy(update={"name": "w"}),)
    )
    assert holds_both.waiting == frozenset(), "a tool a held toolset holds never waits"


@pytest.mark.parametrize(
    ("shape", "refusal"),
    [
        ({"tools": ("lookup", LOAD_TOOLSET)}, "when, and only when, it defers"),
        ({"toolsets": (READS,), "deferred": (READS,)}, "each toolset once"),
        (
            {
                "deferred": (WRITES,),
                "done_rule": DoneRule.RESULT_TOOL,
                "result_tool": "note",
            },
            "never waits for a load",
        ),
        ({"tools": ("lookup", "lookup")}, "each tool once"),
    ],
)
def test_a_kind_refuses_a_deferral_it_cannot_keep(shape: dict[str, object], refusal: str) -> None:
    with pytest.raises(ValueError, match=refusal):
        operator(**shape)


def test_a_catalog_refuses_two_toolsets_under_one_version() -> None:
    other = WRITES.model_copy(update={"tools": ("note",)})
    with pytest.raises(ValueError, match="declared twice"):
        AgentKindCatalog(kinds=(DEFERS, operator(version=2, toolsets=(other,))))


def test_a_toolset_has_a_one_line_summary_and_never_holds_the_load() -> None:
    with pytest.raises(ValueError, match="one line"):
        Toolset(name="x", version=1, summary="two\nlines", tools=("lookup",))
    with pytest.raises(ValueError, match="no toolset holds"):
        Toolset(name="x", version=1, summary="a load", tools=(LOAD_TOOLSET,))


# Check 1: built from toolsets, the request's bytes are those of the flat list.


async def test_a_kind_built_from_toolsets_renders_the_bytes_of_its_flat_tool_list(
    tmp_path: Path,
) -> None:
    bodies: list[list[bytes]] = []
    for index, kind in enumerate((FLAT, BUILT)):
        loop = loop_over(tmp_path / str(index), kinds=(kind,))
        session_id = await loop.start("operator")
        await loop.say(session_id, "What is the total?")
        loop.anthropic.add(reply(use("lookup", use_id="use_1")), reply(said("It is 12.")))
        await ran(loop, session_id)
        bodies.append([json.dumps(request_body(c)[0]).encode() for c in loop.anthropic.calls])
    flat, built = bodies
    assert len(flat) == 2 and flat == built


# Check 2: a deferred toolset loads, recorded, and the history replays.


async def test_a_deferred_toolset_loads_as_a_recorded_switch_and_its_history_replays(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path, kinds=(DEFERS,))
    session_id = await loop.start("operator")
    await loop.say(session_id, "Note the total.")
    loop.anthropic.add(
        reply(use("note", use_id="use_early")),
        reply(call("load_toolset", toolset="writes")),
        reply(use("note", use_id="use_late")),
        reply(said("Noted.")),
    )

    steps = await ran(loop, session_id)

    first, early, loaded, late = loop.anthropic.calls
    assert names(first) == [LOAD_TOOLSET, "lookup", "slow"]
    described = first.tools[0].description.splitlines()
    assert described[1:] == ["- writes: Write a note, and send it out."], "one line"
    responses = [step for step in steps if step.type is StepType.TOOL_RESPONSE]
    assert failure_of(responses[0]) is not None, "a waiting tool is not callable"
    assert early.tools == first.tools
    switches = [step for step in steps if step.type is StepType.SWITCHED]
    assert len(switches) == 1
    (switch,) = switches
    assert isinstance(switch.header, SwitchedHeader)
    assert switch.header.toolset == ToolsetSwitch(name="writes", version=1, tools=("note", "send"))
    assert steps[steps.index(switch) - 1].id == responses[1].id, "in the load's own append"
    assert switch.refs == (responses[1].responds_to,)
    assert loaded.tools[: len(first.tools)] == first.tools, "the old block, byte for byte"
    assert names(loaded)[len(first.tools) :] == ["note", "send"], "then the new tools"
    assert late.tools == loaded.tools
    assert failure_of(responses[2]) is None and loop.tools["note"].ran_as, "callable now"
    assert await replayed(loop, DEFERS, session_id) == loop.anthropic.calls


# Check 3: only a recorded load changes the tool definitions of a session.


def changes(calls: Sequence[ModelCall]) -> list[tuple[list[str], list[str]]]:
    return [(names(a), names(b)) for a, b in pairwise(calls) if a.tools != b.tools]


async def test_a_sessions_tool_definitions_change_only_by_a_recorded_load(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path, kinds=(DEFERS,))
    session_id = await loop.start("operator")
    for question, replies in (
        ("What is the total?", [reply(use("lookup")), reply(said("It is 12."))]),
        (
            "Note it.",
            [
                reply(call("load_toolset", toolset="nothing")),
                reply(call("load_toolset", toolset="writes")),
                reply(call("load_toolset", toolset="writes")),
                reply(use("note")),
                reply(said("Noted.")),
            ],
        ),
        ("Note it again.", [reply(use("note")), reply(said("Noted again."))]),
    ):
        # Each message is a loop of its own, and each run builds its registry
        # again from the session and its history.
        await loop.say(session_id, question)
        loop.anthropic.add(*replies)
        await ran(loop, session_id)

    steps = await loop.history(session_id)
    calls = loop.anthropic.calls
    assert len(calls) == 9
    assert changes(calls) == [
        ([LOAD_TOOLSET, "lookup", "slow"], [LOAD_TOOLSET, "lookup", "slow", "note", "send"])
    ], "one change: the load"
    switches = [step for step in steps if step.type is StepType.SWITCHED]
    assert len(switches) == 1, "a refused load and a second one switch nothing"
    responses = [step for step in steps if step.type is StepType.TOOL_RESPONSE]
    assert failure_of(responses[1]) is ToolFailure.INVALID_INPUT
    again = responses[3].as_tool_response().parts
    assert any(isinstance(p, TextBlock) and "already yours" in p.text for p in again)
    assert await replayed(loop, DEFERS, session_id) == calls


# Check 5: a response that loads a toolset and calls one of its tools.


class Lost(Exception):
    """The run is lost once the load is stored, before the next call is
    answered."""


@pytest.mark.parametrize("lost", [False, True], ids=["one-run", "after-a-lost-run"])
async def test_a_call_of_a_tool_its_own_turn_loads_is_not_offered_and_the_session_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lost: bool
) -> None:
    loop = loop_over(tmp_path, kinds=(DEFERS,))
    session_id = await loop.start("operator")
    await loop.say(session_id, "Note the total.")
    loop.anthropic.add(
        reply(call("load_toolset", toolset="writes"), use("note", use_id="use_early")),
        reply(use("note", use_id="use_late")),
        reply(said("Noted.")),
    )
    if lost:
        append = loop.managers.steps.append_steps

        async def lost_after_the_load(
            ctx: TenantContext, session_id: UUID, epoch: int, steps: Sequence[Step]
        ) -> tuple[Step, ...]:
            stored = await append(ctx, session_id, epoch, steps)
            if any(step.type is StepType.SWITCHED for step in stored):
                raise Lost
            return stored

        monkeypatch.setattr(loop.managers.steps, "append_steps", lost_after_the_load)
        with pytest.raises(Lost):
            await loop.loops.run(loop.owner, session_id)
        monkeypatch.undo()

    steps = await ran(loop, session_id)

    first, loaded, last = loop.anthropic.calls
    answers = {step.responds_to: step for step in steps if step.type is StepType.TOOL_RESPONSE}
    early, late = (
        step
        for step in steps
        if isinstance(step.header, ToolRequestHeader) and step.header.tool == "note"
    )
    refused = answers[early.id]
    assert failure_of(refused) is ToolFailure.INVALID_INPUT, "not offered when it was made"
    parts = refused.as_tool_response().parts
    assert any(isinstance(p, TextBlock) and "no tool named 'note'" in p.text for p in parts)
    assert loaded.tools[: len(first.tools)] == first.tools
    assert names(loaded)[len(first.tools) :] == ["note", "send"], "the next request has them"
    assert failure_of(answers[late.id]) is None and len(loop.tools["note"].ran_as) == 1
    assert last.tools == loaded.tools
    assert await replayed(loop, DEFERS, session_id) == loop.anthropic.calls


def test_a_sub_agent_cut_to_a_parent_without_the_load_still_loads_what_it_holds() -> None:
    cut = ("lookup", "note")  # its parent holds neither `load_toolset` nor `send`
    assert callable_tools(cut, DEFERS.waiting, ()) == {"lookup", LOAD_TOOLSET}
    switch = ToolsetSwitch(name="writes", version=1, tools=("note",))
    assert callable_tools(cut, DEFERS.waiting, (switch,)) == {"lookup", "note", LOAD_TOOLSET}


def test_kind_prompts_render_a_loads_tools_after_the_rest() -> None:
    catalog = [LoadToolsetToolImpl(), *tools().values()]
    held = callable_tools(DEFERS.tools, DEFERS.waiting, ())
    before = kind_prompts(DEFERS, ToolRegistry(t for t in catalog if t.spec.name in held))
    switch = ToolsetSwitch(name="writes", version=1, tools=("note", "send"))
    held = callable_tools(DEFERS.tools, DEFERS.waiting, (switch,))
    after = kind_prompts(DEFERS, ToolRegistry(t for t in catalog if t.spec.name in held), (switch,))
    assert after.tools == before.tools
    assert [tool.name for tool in after.loaded] == ["note", "send"]
