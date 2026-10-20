"""What the scene shows, computed from what the engine recorded and
nothing else: the steps of each session, the usage records, the budget,
and the writer epoch. The demo keeps no counter of its own. Each number
here is a query over the record, so the same record always gives the
same numbers, and the record cut at any step gives them as they stood
then.
"""

import heapq
import json
from collections import Counter
from dataclasses import dataclass
from uuid import UUID

from acme.om.agent_steps.types.agent_step import AgentStep, AgentStepType
from acme.om.agent_steps.types.header import (
    LoopEndedHeader,
    ModelRequestHeader,
    ParkedHeader,
    ToolRequestHeader,
    ToolResponseHeader,
)
from dreamcatcher.engine import Record

TYPES = (
    "message",
    "model_request",
    "model_response",
    "tool_request",
    "tool_response",
    "parked",
    "resumed",
    "control",
    "loop_ended",
)
"""The step types the ledger counts, in the order the check lists them."""


@dataclass(frozen=True)
class Ledger:
    """The numbers: each one a query over the record."""

    steps: tuple[tuple[str, int], ...]
    loops: int
    model_calls: int
    tool_calls: int
    tokens_in: int
    tokens_out: int
    tokens_cache: int
    cost_micros: int
    cap_micros: int | None
    epoch: int

    @property
    def total_steps(self) -> int:
        return sum(n for _, n in self.steps)


def recovered(step: AgentStep) -> bool:
    """A call a new run settled by its effect: the edit found its key
    applied, or the engine answered it as interrupted."""
    header = step.header
    if not isinstance(header, ToolResponseHeader):
        return False
    if header.interrupted:
        return True
    parts = step.as_tool_response().parts
    try:
        said = json.loads(parts[0].text) if parts else {}
    except (ValueError, AttributeError):
        return False
    return isinstance(said, dict) and said.get("replayed") is True


def state_of(steps: list[AgentStep]) -> str:
    """The session's state as its history says it."""
    if not steps:
        return "idle"
    last = steps[-1]
    header = last.header
    if isinstance(header, LoopEndedHeader):
        return f"idle · {header.outcome.value}"
    if isinstance(header, ParkedHeader):
        return f"parked · {header.park.reason.value}"
    if isinstance(header, ModelRequestHeader):
        return "calling the model"
    if isinstance(header, ToolRequestHeader):
        return f"running {header.tool}"
    if last.type is AgentStepType.MESSAGE:
        return "pending"
    return "running"


def project(record: Record) -> Ledger:
    root = record.steps[record.root]
    every = [step for steps in record.steps.values() for step in steps]
    types = Counter(step.type.value for step in every)
    usage = record.usage
    return Ledger(
        steps=tuple((t, types.get(t, 0)) for t in TYPES),
        loops=sum(1 for step in root if step.type is AgentStepType.LOOP_ENDED),
        model_calls=len(usage),
        tool_calls=types.get("tool_request", 0),
        tokens_in=sum(r.input_tokens + r.cache_write_tokens for r in usage),
        tokens_out=sum(r.output_tokens + r.thinking_tokens for r in usage),
        tokens_cache=sum(r.cache_read_tokens for r in usage),
        cost_micros=sum(r.cost_micros or 0 for r in usage),
        cap_micros=record.budget.cost_micros if record.budget is not None else None,
        epoch=record.epoch,
    )


def sessions_of(record: Record) -> list[UUID]:
    """The root, then each sub-agent in the order it was spawned."""
    return [record.root, *(child.id for child in record.children)]


def order(record: Record) -> list[AgentStep]:
    """Every step of every session, in the order the engine wrote them: by
    the time each was written, each session's own steps kept in their
    order, and a tie between sessions given to the one spawned first."""
    rank = {sid: n for n, sid in enumerate(sessions_of(record))}
    histories = [record.steps.get(sid, []) for sid in rank]
    return list(heapq.merge(*histories, key=lambda step: (step.created_at, rank[step.agent_session_id])))


def until(record: Record, seen: dict[UUID, int]) -> Record:
    """The history as it stood when each session held `seen[sid]` steps:
    those steps, the sub-agents spawned by then, and the usage written by
    then. The budget and the epoch stay the record's; the caller sets
    them as they stood at that moment."""
    steps = {sid: record.steps.get(sid, [])[: seen.get(sid, 0)] for sid in sessions_of(record)}
    written = [history[-1].created_at for history in steps.values() if history]
    children = [child for child in record.children if steps[child.id]]
    usage = [r for r in record.usage if written and r.created_at <= max(written)]
    return Record(record.root, steps, children, usage, record.holds, record.budget, record.tally, record.epoch)
