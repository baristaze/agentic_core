"""The tape: the run as the scene plays it.

The writer reads what the engine recorded and nothing else. The tape
holds every step of every session, in the order the engine wrote them,
each with the numbers `projection` computes from the record cut at that
step. Three moments are not steps, and each comes from what the engine
answered: the budget's raise, at the time the budget changed; a runner
taking over the session while the runner before it still held a call;
and that runner's late write, refused when its run returned.

The tape carries no id and no time, so the same run writes the same
bytes, and the page computes no number of its own.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from acme.om.agent_steps.types.agent_step import Actor, AgentStep, AgentStepType
from acme.om.agent_steps.types.content import ToolUseBlock
from acme.om.agent_steps.types.header import (
    InputHeader,
    LoopEndedHeader,
    ModelRequestHeader,
    ParkedHeader,
    ToolRequestHeader,
    ToolResponseHeader,
)
from acme.om.agents.types.run import LoopRun, RunEnd
from acme.om.spend_budgets.types.spend_budget import SpendBudget
from dreamcatcher.acts import Proof, Script
from dreamcatcher.engine import FIXER, Record
from dreamcatcher.projection import Ledger, order, project, recovered, sessions_of, state_of, until

TAPE = Path(__file__).resolve().parent.parent / "scene" / "tape.js"

NUMBERS = ("loops", "model_calls", "tool_calls", "tokens_in", "tokens_out", "tokens_cache", "cost_micros", "cap_micros", "epoch")
"""The ledger's numbers the tape carries, after the total of the steps."""

SHOWN = ("path", "text", "passed", "detail", "replayed", "question", "title", "old", "new")
"""What a tool's input or output may show on the page; never an id."""

Event = dict[str, Any]


def numbers(ledger: Ledger) -> dict[str, int | None]:
    return {"steps": ledger.total_steps, **{name: getattr(ledger, name) for name in NUMBERS}}


def shown(raw: object) -> dict[str, Any]:
    return {str(k): v for k, v in raw.items() if k in SHOWN} if isinstance(raw, Mapping) else {}


@dataclass(frozen=True)
class Mark:
    """A moment that is not a step: where it falls in the order (before the
    step at `index`), what the page shows, and the epoch and the budget
    that stood then."""

    index: int
    fields: Event
    epoch: int
    budget: SpendBudget | None


class Writer:
    def __init__(self, record: Record, proof: Proof, script: Script) -> None:
        self.record = record
        self.sessions = sessions_of(record)
        self.rank = {sid: n for n, sid in enumerate(self.sessions)}
        self.steps = order(record)
        versions = {b.version: b for b in (proof.budget, record.budget) if b is not None}
        self.budgets: list[SpendBudget] = [versions[v] for v in sorted(versions)]
        begun = [(proof.begun[name], run) for name, run in proof.runs.items() if name in proof.begun]
        self.runs: list[tuple[datetime, LoopRun]] = sorted(begun, key=lambda r: r[1].epoch)
        self.by_id = {step.id: step for step in self.steps}
        self.uses: dict[str, ToolUseBlock] = {
            use.id: use for step in self.steps if step.type is AgentStepType.MODEL_RESPONSE for use in step.as_tool_uses()
        }
        self.opens = {proof.acts[act.name]: n for n, act in enumerate(script.acts) if act.name in proof.acts}

    def epoch_at(self, moment: datetime) -> int:
        """The writer epoch at `moment`: the epoch of the latest run of the
        root begun by then."""
        return max((run.epoch for began, run in self.runs if began <= moment), default=0)

    def budget_at(self, moment: datetime) -> SpendBudget | None:
        stood = [b for b in self.budgets if b.updated_at <= moment]
        return stood[-1] if stood else (self.budgets[0] if self.budgets else None)

    def after(self, moment: datetime) -> int:
        """The index of the first step written after `moment`."""
        return next((i for i, step in enumerate(self.steps) if step.created_at > moment), len(self.steps))

    def marks(self) -> list[Mark]:
        marks = [
            Mark(self.after(b.updated_at), {"k": "raise", "cap": b.cost_micros}, self.epoch_at(b.updated_at), b)
            for b in self.budgets[1:]
        ]
        root = self.record.steps[self.record.root]
        last = self.steps[-1].created_at if self.steps else None
        for _, lost in self.runs:
            taker = next(((began, run) for began, run in self.runs if run.epoch > lost.epoch), None)
            if lost.end is not RunEnd.STALE or taker is None or last is None:
                continue
            began, run = taker
            held = [step for step in root if step.created_at < began]
            tool = held[-1].header.tool if held and isinstance(held[-1].header, ToolRequestHeader) else ""
            took = {"k": "takeover", "lost": lost.epoch, "to": run.epoch, "tool": tool}
            marks.append(Mark(self.after(began), took, run.epoch, self.budget_at(began)))
            refused = {"k": "refused", "epoch": lost.epoch, "tool": tool}
            marks.append(Mark(len(self.steps), refused, self.epoch_at(last), self.budget_at(last)))
        return sorted(marks, key=lambda m: m.index)

    def event(self, fields: Event, seen: dict[Any, int], epoch: int, budget: SpendBudget | None) -> Event:
        cut = replace(until(self.record, seen), budget=budget, epoch=epoch)
        states = [state_of(cut.steps[sid]) for sid in self.sessions if cut.steps[sid]]
        return {**fields, "n": numbers(project(cut)), "state": states}

    def write(self) -> list[Event]:
        events: list[Event] = []
        marks = self.marks()
        seen = dict.fromkeys(self.sessions, 0)
        before: dict[int, AgentStep] = {}
        for index in range(len(self.steps) + 1):
            for mark in (m for m in marks if m.index == index):
                events.append(self.event(mark.fields, seen, mark.epoch, mark.budget))
            if index == len(self.steps):
                return events
            step = self.steps[index]
            session = self.rank[step.agent_session_id]
            seen[step.agent_session_id] += 1
            fields = {**self.kind(step, session, before.get(session)), "s": session, "seq": step.seq}
            events.append(self.event(fields, seen, self.epoch_at(step.created_at), self.budget_at(step.created_at)))
            before[session] = step
        return events

    def kind(self, step: AgentStep, session: int, before: AgentStep | None) -> Event:
        header = step.header
        if step.type is AgentStepType.MESSAGE:
            return self.message(step, session, before)
        if isinstance(header, ModelRequestHeader):
            hold = self.record.holds.get(header.spend_hold_id) if header.spend_hold_id is not None else None
            return {"k": "model_request", "hold": hold}
        if step.type is AgentStepType.MODEL_RESPONSE:
            return {"k": "model_response", "text": " ".join(step.as_text().split()), "calls": len(step.as_tool_uses())}
        if isinstance(header, ToolRequestHeader):
            use = self.uses.get(header.tool_use_id)
            return {"k": "tool_request", "tool": header.tool, "input": shown(use.input if use is not None else {})}
        if isinstance(header, ToolResponseHeader):
            request = self.by_id.get(step.responds_to) if step.responds_to is not None else None
            asked = request.header if request is not None else None
            parts = step.as_tool_response().parts
            try:
                effect = shown(json.loads(parts[0].text)) if parts else {}
            except (ValueError, AttributeError):
                effect = {}
            said: Event = {"k": "tool_response", "tool": asked.tool if isinstance(asked, ToolRequestHeader) else ""}
            said["effect"] = effect
            if header.failure is not None:
                said["failure"] = header.failure.value
            if recovered(step):
                said["recovered"] = True
            return said
        if isinstance(header, ParkedHeader):
            return {"k": "parked", "reason": header.park.reason.value}
        if isinstance(header, LoopEndedHeader):
            return {"k": "loop_ended", "outcome": header.outcome.value}
        return {"k": step.type.value}

    def message(self, step: AgentStep, session: int, before: AgentStep | None) -> Event:
        """A person's message, steer, or answer; a sub-agent's task from its
        parent; or a sub-agent's report to its parent."""
        header = step.header
        if step.actor is not Actor.PERSON:
            agent = header.agent if isinstance(header, InputHeader) else None
            child = self.rank.get(agent.agent_session_id, 0) if agent is not None else 0
            if child > session:
                return {"k": "report", "from": child}
            return {"k": "task", "text": step.as_text()}
        said: Event = {"k": "message", "text": step.as_text()}
        if before is not None and before.type is AgentStepType.PARKED:
            said["k"] = "answer"
        elif before is not None and before.type is not AgentStepType.LOOP_ENDED:
            said["k"] = "steer"
        if session == 0 and step.seq in self.opens:
            said["act"] = self.opens[step.seq]
        return said


def write(record: Record, proof: Proof, script: Script) -> dict[str, Any]:
    sessions = [{"kind": FIXER.name}, *({"kind": c.kind, "title": c.title.said()} for c in record.children)]
    acts = [{"title": act.title, "about": act.about} for act in script.acts]
    return {"sessions": sessions, "acts": acts, "events": Writer(record, proof, script).write()}


def render(tape: dict[str, Any]) -> str:
    """The tape as a script that sets one global, so the page reads it
    from a file with no request; one event a line."""
    head = json.dumps({k: v for k, v in tape.items() if k != "events"}, separators=(",", ":"))
    rows = ",\n".join(json.dumps(event, separators=(",", ":")) for event in tape["events"])
    return (
        "// The run as the scene plays it: every step the engine recorded, in the\n"
        "// order it wrote them, each with the numbers the record gives at that step.\n"
        "// `make demo` writes it, and `make demo-check` holds it to a fresh run.\n"
        f'window.TAPE = {head[:-1]},"events":[\n{rows}\n]}};\n'
    )
