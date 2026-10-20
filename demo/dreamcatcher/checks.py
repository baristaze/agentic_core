"""What `--check` holds, each claim read back from what the engine
recorded: the history of each session, the usage records, the budget's
tally, and the writer epoch. A claim the run did not exercise fails.
"""

import itertools
import socket
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from acme.integrations.model_providers.scripted import ModelProviderScriptedImpl
from acme.integrations.model_providers.types import ProviderName
from acme.om.agent_steps.types.agent_step import Actor, AgentStep, AgentStepType
from acme.om.agent_steps.types.header import (
    InputHeader,
    LoopEndedHeader,
    LoopOutcome,
    ModelResponseHeader,
    ParkedHeader,
    ParkReason,
    ToolRequestHeader,
)
from acme.om.agents.types.run import RunEnd
from acme.om.base import new_id
from acme.om.exceptions import StaleWriter
from dreamcatcher.acts import Proof, Script
from dreamcatcher.engine import REVIEWER, Engine, Record
from dreamcatcher.projection import project, recovered
from dreamcatcher.tape import render

Claim = tuple[str, bool]
OUTBOUND = ("socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex")


def refuse_outbound() -> None:
    """Every outbound connection this process tries is refused, from here
    on: a name lookup or a connect raises before it leaves."""

    def hook(event: str, args: tuple[object, ...]) -> None:
        if event in OUTBOUND:
            raise ConnectionRefusedError(f"dreamcatcher refuses {event}")

    sys.addaudithook(hook)


def outbound_refused() -> bool:
    """A probe: a connection is refused here. Its address is one kept for
    documentation, so a guard that failed would reach no one."""
    try:
        socket.create_connection(("192.0.2.1", 443), timeout=1).close()
    except ConnectionRefusedError as refused:
        return "dreamcatcher refuses" in str(refused)
    except OSError:
        return False
    return False


def refuse_paid_providers() -> None:
    """Building a provider that calls a paid API fails from here on."""
    from acme.integrations.model_providers.anthropic import ModelProviderAnthropicImpl
    from acme.integrations.model_providers.openai import ModelProviderOpenAIImpl

    def refuse(name: str) -> Callable[..., None]:
        def built(*args: object, **kwargs: object) -> None:
            raise RuntimeError(f"{name} calls a paid API, and the demo builds none")

        return built

    for paid in (ModelProviderAnthropicImpl, ModelProviderOpenAIImpl):
        paid.__init__ = refuse(paid.__name__)


def offline(engine: Engine, keyed: bool, probe: bool) -> list[Claim]:
    adapters = [engine.providers.get(p) for p in ProviderName]
    return [
        ("a provider's key is set in the environment", keyed),
        ("every outbound connection is refused", probe),
        ("the only model provider built is the scripted twin", all(isinstance(a, ModelProviderScriptedImpl) for a in adapters)),
    ]


def after(steps: list[AgentStep], seq: int) -> list[AgentStep]:
    return [step for step in steps if step.seq > seq]


def first(steps: list[AgentStep], kind: AgentStepType, seq: int = 0) -> AgentStep | None:
    return next((step for step in steps if step.type is kind and step.seq > seq), None)


def tool_calls(steps: list[AgentStep]) -> list[str]:
    return [step.header.tool for step in steps if isinstance(step.header, ToolRequestHeader)]


async def acts(engine: Engine, script: Script, proof: Proof, record: Record) -> list[Claim]:
    assert proof.root is not None and proof.steer is not None and proof.answer is not None
    root = record.steps[proof.root]
    by_id = {step.id: step for step in root}
    act1 = [s for s in root if proof.acts["simple"] <= s.seq < proof.acts["smart"]]
    act3 = after(root, proof.acts["power"] - 1)
    ended = [s.header.outcome for s in act1 if isinstance(s.header, LoopEndedHeader)]
    claims: list[Claim] = [
        (
            "act 1 · one loop: plan, read, run, edit, run, done",
            ended == [LoopOutcome.SUCCEEDED]
            and tool_calls(act1) == ["write_agent_plan", "read_file", "run_check", "edit_file", "run_check"],
        )
    ]

    steer = by_id[proof.steer]
    running = [s for s in root if isinstance(s.header, ToolRequestHeader) and s.seq < steer.seq]
    answered_after = any(s.responds_to == running[-1].id and s.seq > steer.seq for s in root) if running else False
    request = first(root, AgentStepType.MODEL_REQUEST, steer.seq)
    claims.append(
        (
            "act 2 · a steer sent mid tool call is read by the next model request",
            answered_after and request is not None and steer.id in request.refs,
        )
    )

    answer = by_id[proof.answer]
    parked = [s for s in root if isinstance(s.header, ParkedHeader) and s.seq < answer.seq]
    resumed = first(root, AgentStepType.RESUMED, answer.seq)
    request = first(root, AgentStepType.MODEL_REQUEST, answer.seq)
    claims.append(
        (
            "act 2 · a park on person is cleared by the person's answer",
            bool(parked)
            and isinstance(parked[-1].header, ParkedHeader)
            and parked[-1].header.park.reason is ParkReason.PERSON
            and resumed is not None
            and request is not None
            and answer.id in request.refs,
        )
    )

    children = record.children
    reports = [
        s for s in root if isinstance(s.header, InputHeader) and s.header.agent is not None and s.type is AgentStepType.MESSAGE
    ]
    reporters = {s.header.agent.agent_session_id for s in reports if isinstance(s.header, InputHeader) and s.header.agent}
    done = all(
        isinstance(record.steps[c.id][-1].header, LoopEndedHeader)
        and record.steps[c.id][-1].header.outcome is LoopOutcome.SUCCEEDED
        for c in children
    )
    reading = first(root, AgentStepType.MODEL_REQUEST, max((s.seq for s in reports), default=0))
    claims.append(
        (
            "act 2 · two sub-agents run, and their reports reach the parent's history",
            len(children) == 2
            and all(c.kind == REVIEWER.name for c in children)
            and done
            and reporters == {c.id for c in children}
            and reading is not None
            and {s.id for s in reports} <= set(reading.refs),
        )
    )

    budget = record.budget
    park = next((s for s in act3 if s.actor is Actor.ENGINE), None)
    unparked = first(act3, AgentStepType.RESUMED)
    request = first(act3, AgentStepType.MODEL_REQUEST)
    claims.append(
        (
            "act 3 · a park on spend_budget is written before the model request it holds",
            park is not None
            and isinstance(park.header, ParkedHeader)
            and park.header.park.reason is ParkReason.SPEND_BUDGET
            and request is not None
            and park.seq < request.seq,
        )
    )
    claims.append(
        (
            "act 3 · a raise of the budget clears it",
            budget is not None
            and budget.cost_micros == script.raised_micros
            and unparked is not None
            and request is not None
            and park is not None
            and park.seq < unparked.seq < request.seq,
        )
    )

    edits = [s for s in act3 if isinstance(s.header, ToolRequestHeader) and s.header.tool == "edit_file"]
    answers = [s for s in act3 if edits and s.responds_to == edits[0].id]
    key = str(edits[0].id) if edits else ""
    lost, power = proof.runs["lost"], proof.runs["power"]
    refused = False
    if answers:
        try:
            stale = answers[0].model_copy(update={"id": new_id()})
            await engine.managers.agent_steps.append_agent_steps(engine.owner, proof.root, lost.epoch, [stale])
        except StaleWriter:
            refused = True
    claims.append(
        (
            "act 3 · a runner replaced mid tool call: the effect applied once, the stale writer refused",
            len(edits) == 1
            and len(answers) == 1
            and recovered(answers[0])
            and engine.workspace.attempts.count(key) == 2
            and list(engine.workspace.applied).count(key) == 1
            and engine.workspace.files["NOTES.md"].count("TOTAL is 20") == 1
            and lost.end is RunEnd.STALE
            and power.outcome is LoopOutcome.SUCCEEDED
            and lost.epoch < power.epoch == record.epoch
            and refused,
        )
    )
    return claims


async def read_back(engine: Engine, record: Record) -> dict[str, int | None]:
    """The closing numbers, read back by other queries than the ledger's:
    the steps counted afresh, the tokens from the responses' own usage,
    the spend from the budget's tally, and the epoch from the cursor."""
    sessions = [record.root, *(c.id for c in record.children)]
    histories = [await engine.history(sid) for sid in sessions]
    steps = [step for history in histories for step in history]
    responses = [s.header for s in steps if isinstance(s.header, ModelResponseHeader) and not s.header.abandoned]
    usage = [h.usage for h in responses if h.usage is not None]
    return {
        "steps": len(steps),
        "loops": sum(1 for s in histories[0] if s.type is AgentStepType.LOOP_ENDED),
        "model_calls": len(responses),
        "tool_calls": sum(1 for s in steps if s.type is AgentStepType.TOOL_REQUEST),
        "tokens_in": sum(u.input + u.cache_write for u in usage),
        "tokens_out": sum(u.output + u.thinking for u in usage),
        "tokens_cache": sum(u.cache_read for u in usage),
        "cost_micros": record.tally.spent_cost_micros if record.tally is not None else None,
        "cap_micros": record.budget.cost_micros if record.budget is not None else None,
        "epoch": await engine.epoch(record.root),
    }


async def ledger(engine: Engine, record: Record) -> list[Claim]:
    """The closing ledger against the record read back by other queries:
    the steps counted afresh, the tokens from the responses' own usage,
    and the spend from the budget's tally."""
    shown = project(record)
    sessions = [record.root, *(c.id for c in record.children)]
    steps = [step for sid in sessions for step in await engine.history(sid)]
    counted = Counter(step.type.value for step in steps)
    responses = [s.header for s in steps if isinstance(s.header, ModelResponseHeader) and not s.header.abandoned]
    usage = [h.usage for h in responses if h.usage is not None]
    tally = record.tally
    return [
        ("steps by type", all(counted.get(t, 0) == n for t, n in shown.steps) and shown.total_steps == len(steps)),
        ("model calls", shown.model_calls == len(responses) == len(record.usage)),
        (
            "tokens",
            (shown.tokens_in, shown.tokens_out, shown.tokens_cache)
            == (
                sum(u.input + u.cache_write for u in usage),
                sum(u.output + u.thinking for u in usage),
                sum(u.cache_read for u in usage),
            ),
        ),
        (
            "cost",
            tally is not None and shown.cost_micros == tally.spent_cost_micros and tally.held_cost_micros == 0,
        ),
    ]


MESSAGES = ("message", "steer", "answer", "task", "report")
"""The tape's kinds of a message step."""


async def tape(engine: Engine, record: Record, written: dict[str, Any], committed: Path) -> list[Claim]:
    """The tape against the record read back: every step of every session
    once, each session's in its order, each kind its step's type, and the
    write times never going back; its last numbers equal to the closing
    numbers read back; each act on it; and the committed tape the one this
    run writes."""
    sessions = [record.root, *(c.id for c in record.children)]
    histories = [await engine.history(sid) for sid in sessions]
    events: list[dict[str, Any]] = written["events"]
    placed = [e for e in events if "seq" in e]
    steps = {(n, step.seq): step for n, history in enumerate(histories) for step in history}
    each = all([e["seq"] for e in placed if e["s"] == n] == [s.seq for s in history] for n, history in enumerate(histories))
    typed = all(
        (e["s"], e["seq"]) in steps and steps[e["s"], e["seq"]].type.value == ("message" if e["k"] in MESSAGES else e["k"])
        for e in placed
    )
    times = [steps[e["s"], e["seq"]].created_at for e in placed if (e["s"], e["seq"]) in steps]
    onward = all(a <= b for a, b in itertools.pairwise(times))
    kinds = [e["k"] for e in events]
    parks = {e.get("reason") for e in events if e["k"] == "parked"}
    acted = (
        all(k in kinds for k in ("steer", "answer", "raise", "takeover", "refused"))
        and kinds.count("report") == 2
        and {"person", "spend_budget"} <= parks
    )
    closing = await read_back(engine, record)
    fresh = committed.is_file() and committed.read_text() == render(written)
    return [
        ("every step of every session, in the order the engine wrote them", each and typed and onward),
        ("its last numbers equal the closing numbers read back", bool(events) and events[-1]["n"] == closing),
        ("each act on it: a steer, an answer, two reports, the budget's park and raise, a takeover, a refused write", acted),
        ("the committed tape is the one this run writes (make demo rewrites it)", fresh),
    ]
