"""What a frame shows, computed from what the engine recorded and nothing
else: the steps of each session, the usage records, the holds, and the
budget. The demo keeps no counter of its own. Each number here is a
query over the record, so the same record always gives the same frame,
and a replay of the history redraws the run.
"""

import itertools
import json
from collections import Counter
from dataclasses import dataclass
from uuid import UUID

from acme.om.agent_sessions.limits import tally_loop
from acme.om.agent_steps.types.agent_step import Actor, AgentStep, AgentStepType
from acme.om.agent_steps.types.header import (
    InputHeader,
    LoopEndedHeader,
    ModelRequestHeader,
    ModelResponseHeader,
    ParkedHeader,
    ToolRequestHeader,
    ToolResponseHeader,
)
from acme.om.language_models.impl.resolver import DEFAULT_TABLE
from dreamcatcher.engine import FIXER, Record

WINDOWS = {f"{row.fill.provider.value}/{row.fill.model}": row.fill.context_window for row in DEFAULT_TABLE}
"""Each model's context window, as the engine's default table gives it."""

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
"""The step types a frame counts, in the order the panel lists them."""


@dataclass(frozen=True)
class Slice:
    """One step on a ring: its kind, which picks its color, and the
    indexes of the steps it points at."""

    kind: str
    links: tuple[int, ...]


@dataclass(frozen=True)
class Ring:
    slices: tuple[Slice, ...]
    reports: tuple[int, ...] = ()
    """For a sub-agent's ring: where its report landed on the root's ring."""
    title: str = ""
    state: str = ""


@dataclass(frozen=True)
class Ledger:
    """The numbers: each one a query over the record."""

    steps: tuple[tuple[str, int], ...]
    loops: int
    open_loop: bool
    model_calls: int
    tool_calls: int
    tokens_in: int
    tokens_out: int
    tokens_cache: int
    cost_micros: int
    cap_micros: int | None
    held_micros: int
    hold_micros: int | None
    hold_call: int
    latency_ms: int
    last_latency_ms: int
    wall_s: float
    parks: tuple[tuple[str, int], ...]
    steers: int
    recovered: int
    epoch: int
    guard: int
    guard_limit: int
    window: float

    @property
    def total_steps(self) -> int:
        return sum(n for _, n in self.steps)


@dataclass(frozen=True)
class Frame:
    ring: Ring
    moons: tuple[Ring, ...]
    ledger: Ledger
    state: str
    said: str


def kind_of(step: AgentStep) -> str:
    """A slice's kind: its step's type, with a failure, a report, and a
    recovered call told apart."""
    header = step.header
    if isinstance(header, InputHeader) and header.agent is not None:
        return "report"
    if isinstance(header, ToolResponseHeader):
        if header.failure is not None:
            return "failure"
        if recovered(step):
            return "recovered"
    return step.type.value


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


def ring_of(steps: list[AgentStep]) -> tuple[Slice, ...]:
    """Each step a slice; each response linked to its request, and each
    request to the inputs it delivered."""
    index = {step.id: n for n, step in enumerate(steps)}
    slices = []
    for step in steps:
        links = [step.responds_to] if step.responds_to is not None else []
        if step.type is AgentStepType.MODEL_REQUEST:
            links.extend(step.refs)
        slices.append(Slice(kind_of(step), tuple(index[i] for i in links if i in index)))
    return tuple(slices)


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


def steers(steps: list[AgentStep]) -> int:
    """Messages a person wrote while a loop of theirs ran: not a loop's
    first message, and not the answer to a park."""
    count = 0
    for before, step in itertools.pairwise(steps):
        person = step.type is AgentStepType.MESSAGE and step.actor is Actor.PERSON
        if person and before.type not in (AgentStepType.LOOP_ENDED, AgentStepType.PARKED):
            count += 1
    return count


def said(steps: list[AgentStep]) -> str:
    """The latest text the model wrote."""
    for step in reversed(steps):
        if step.type is AgentStepType.MODEL_RESPONSE and step.as_text().strip():
            return " ".join(step.as_text().split())
    return ""


def window_of(steps: list[AgentStep]) -> float:
    """How full the window was at the latest call: what that call read and
    wrote, over its model's context window."""
    by_id = {step.id: step for step in steps}
    for step in reversed(steps):
        header = step.header
        if isinstance(header, ModelResponseHeader) and header.usage is not None:
            request = by_id.get(step.responds_to) if step.responds_to is not None else None
            if request is None or not isinstance(request.header, ModelRequestHeader):
                return 0.0
            u = header.usage
            used = u.input + u.cache_read + u.cache_write + u.output + u.thinking
            return used / WINDOWS.get(request.header.fill, 1_000_000)
    return 0.0


def project(record: Record) -> Frame:
    root = record.steps[record.root]
    every = [step for steps in record.steps.values() for step in steps]
    types = Counter(step.type.value for step in every)
    parks = Counter(step.header.park.reason.value for step in every if isinstance(step.header, ParkedHeader))
    requests = [step for step in root if isinstance(step.header, ModelRequestHeader)]
    last_request = requests[-1].header if requests else None
    usage = sorted(record.usage, key=lambda r: r.created_at)
    tally = record.tally
    loops = sum(1 for step in root if step.type is AgentStepType.LOOP_ENDED)
    open_loop = bool(root) and root[-1].type is not AgentStepType.LOOP_ENDED
    guard = tally_loop(root, root[-1].loop_id).model_calls if root else 0
    hold = None
    if isinstance(last_request, ModelRequestHeader) and last_request.spend_hold_id is not None:
        hold = record.holds.get(last_request.spend_hold_id)
    wall = (every[-1].created_at - min(s.created_at for s in every)).total_seconds() if every else 0.0
    ledger = Ledger(
        steps=tuple((t, types.get(t, 0)) for t in TYPES),
        loops=loops,
        open_loop=open_loop,
        model_calls=len(usage),
        tool_calls=types.get("tool_request", 0),
        tokens_in=sum(r.input_tokens + r.cache_write_tokens for r in usage),
        tokens_out=sum(r.output_tokens + r.thinking_tokens for r in usage),
        tokens_cache=sum(r.cache_read_tokens for r in usage),
        cost_micros=sum(r.cost_micros or 0 for r in usage),
        cap_micros=record.budget.cost_micros if record.budget is not None else None,
        held_micros=tally.held_cost_micros if tally is not None else 0,
        hold_micros=hold,
        hold_call=len(requests),
        latency_ms=sum(r.latency_ms for r in usage),
        last_latency_ms=usage[-1].latency_ms if usage else 0,
        wall_s=wall,
        parks=tuple(sorted(parks.items())),
        steers=steers(root),
        recovered=sum(1 for step in every if recovered(step)),
        epoch=record.epoch,
        guard=guard,
        guard_limit=FIXER.limits.step_guard,
        window=window_of(root),
    )
    moons = []
    index = {step.id: n for n, step in enumerate(root)}
    for child in record.children:
        steps = record.steps.get(child.id, [])
        reports = tuple(
            index[step.id]
            for step in root
            if isinstance(step.header, InputHeader)
            and step.header.agent is not None
            and step.header.agent.agent_session_id == child.id
        )
        moons.append(Ring(ring_of(steps), reports, child.title, state_of(steps)))
    return Frame(Ring(ring_of(root)), tuple(moons), ledger, state_of(root), said(every))


def until(record: Record, count: int) -> Record:
    """The record as it stood when the root held `count` steps: the replay
    of the history alone."""
    root = record.steps[record.root][:count]
    if not root:
        return Record(record.root, {record.root: []}, [], [], {}, record.budget, record.tally, record.epoch)
    moment = root[-1].created_at
    steps: dict[UUID, list[AgentStep]] = {record.root: root}
    for child in record.children:
        steps[child.id] = [s for s in record.steps.get(child.id, []) if s.created_at <= moment]
    children = [c for c in record.children if steps[c.id]]
    usage = [r for r in record.usage if r.created_at <= moment]
    return Record(record.root, steps, children, usage, record.holds, record.budget, record.tally, record.epoch)
