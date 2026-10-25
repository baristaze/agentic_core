"""One point of the run: a number of sessions, each replaying the shape's
turns at once in this process, on one engine. It measures the steps
written a second, each append's latency, each run's start, the key
service's calls for each step written and each step read, and where the
wall time goes."""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from collections import deque
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from acme.om.agent_sessions.types.agent_session import Among
from acme.om.agent_steps.types.agent_step import AgentStep
from acme.om.agent_steps.types.header import LoopOutcome
from acme.om.agents.types.run import RunEnd
from acme.om.privacy.impl.sealed_agent_steps import says_something
from replay.engine import SESSION, Engine, timed_appends
from replay.generate import Run
from replay.profile import Profile, Sampler

RENDERING: dict[str, frozenset[str] | None] = {
    "/acme/om/windows/rules.py": None,
    "/acme/om/windows/impl/manager.py": frozenset({"render_request", "_main", "_drafted", "render_after_overflow"}),
}
"""The engine's functions that turn a history into a model call."""


def said_bytes(step: AgentStep) -> int:
    """What the sealing layer seals of a step, in bytes: its blocks and its
    children, as it encodes them before the seal (`sealed_agent_steps.sealed`)."""
    if not says_something(step):
        return 0
    return len(
        json.dumps(
            {"blocks": step.content.model_dump(mode="json")["blocks"], "children": step.children.model_dump(mode="json")},
            separators=(",", ":"),
        ).encode()
    )


def kept_chars(step: AgentStep) -> int:
    kept = getattr(step.header, "tool_output", None)
    return 0 if kept is None else int(kept.characters)


def for_a_session(task: asyncio.Task[Any]) -> bool:
    """A task that works for a replayed session: it runs in the context a
    session's runs set, as every task the engine starts for it does."""
    context = task.get_context()  # type: ignore[attr-defined]
    return context.get(SESSION) is not None


READER = UUID(int=0)
"""What the read passes set the session to, so the sampler counts their
task as working; no session has it."""


class RunRefused(RuntimeError):
    """A run of the loop that did not end as the session's did."""


async def replay(engine: Engine, runs: list[Run]) -> UUID:
    """One session through every run of its script."""
    agent_session_id = await engine.new_session()
    token = SESSION.set(agent_session_id)
    try:
        engine.twin.scripts[agent_session_id] = deque(reply for run in runs for reply in run.replies)
        for run in runs:
            await engine.say(agent_session_id, run.said)
            result = await engine.run(agent_session_id)
            if result.end is not RunEnd.ENDED or result.outcome is not LoopOutcome.SUCCEEDED:
                raise RunRefused(f"a run ended {result.end.value}, {result.outcome}, parked on {result.park}")
    finally:
        SESSION.reset(token)
    if engine.twin.scripts[agent_session_id]:
        raise RunRefused("the session ended before its script")
    return agent_session_id


HISTORY_PAGE = 50
"""A history page as the API's route reads it by default (`LIMIT_DEFAULT`)."""
LIST_PAGE = 50
"""A page of the session list, as the API's list route reads it."""
LIST_PASSES = 20
"""The session list read whole this many times, so its pages make a
distribution."""


def quantiles_ms(seconds: list[float]) -> dict[str, float]:
    cuts = statistics.quantiles(seconds, n=100) if len(seconds) > 1 else [seconds[0] if seconds else 0.0] * 99
    return {"p50": round(cuts[49] * 1000, 2), "p95": round(cuts[94] * 1000, 2), "p99": round(cuts[98] * 1000, 2)}


@dataclass
class Read:
    """One read pass: its pages, their items, each page's latency, the key
    service's calls, and where its time went."""

    pages: int = 0
    items: int = 0
    seconds: float = 0.0
    latencies: list[float] = field(default_factory=list)
    key_calls: int = 0
    profile: Profile | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "pages": self.pages,
            "items": self.items,
            "items_per_page": round(self.items / max(1, self.pages), 1),
            "seconds": round(self.seconds, 3),
            "pages_per_second": round(self.pages / self.seconds, 1) if self.seconds else 0.0,
            "page_ms": quantiles_ms(self.latencies),
            "key_calls": self.key_calls,
            "key_calls_per_page": round(self.key_calls / max(1, self.pages), 3),
            "profile": None if self.profile is None else self.profile.as_dict(),
        }


@dataclass
class Point:
    sessions: int
    turns: int
    seconds: float
    steps_written: int
    appends: list[float]
    key_calls: int
    keys: dict[str, int]
    history: Read
    listing: Read
    said_bytes: int
    kept_chars: int
    summaries: int
    profile: Profile | None
    run_starts: list[float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "sessions": self.sessions,
            "turns_per_session": self.turns,
            "wall_seconds": round(self.seconds, 3),
            "steps_written": self.steps_written,
            "steps_per_second": round(self.steps_written / self.seconds, 1),
            "appends": len(self.appends),
            "append_ms": quantiles_ms(self.appends),
            "run_start_ms": {
                **quantiles_ms(self.run_starts),
                "last": round(self.run_starts[-1] * 1000, 2) if self.run_starts else 0.0,
                "runs": len(self.run_starts),
            },
            "key_calls": self.key_calls,
            "key_calls_per_step_written": round(self.key_calls / max(1, self.steps_written), 3),
            "key_calls_by_asker": dict(sorted(self.keys.items(), key=lambda kv: -kv[1])),
            "read": {
                "history": self.history.as_dict(),
                "list": self.listing.as_dict(),
                "key_calls_per_step_read": round(self.history.key_calls / max(1, self.history.items), 4),
            },
            "said_bytes": self.said_bytes,
            "kept_chars": self.kept_chars,
            "summaries": self.summaries,
            "profile": None if self.profile is None else self.profile.as_dict(),
        }


async def read_history(engine: Engine, sessions: list[UUID], into: Read, steps: list[AgentStep]) -> None:
    """Each session's history from its start, a page at a time, as the API's
    route reads it: the session, then the page."""
    ctx = engine.ctx()
    for agent_session_id in sessions:
        after = 0
        while True:
            started = time.perf_counter()
            await engine.managers.agent_sessions.get_agent_session(ctx, agent_session_id)
            page = await engine.managers.agent_steps.get_agent_steps(ctx, agent_session_id, after, HISTORY_PAGE)
            into.latencies.append(time.perf_counter() - started)
            into.pages += 1
            into.items += len(page.items)
            steps.extend(page.items)
            if not page.has_more or not page.items:
                break
            after = page.items[-1].seq


async def read_list(engine: Engine, into: Read) -> None:
    """The tenant's session list, a page at a time, as the API's list route
    reads it, whole, `LIST_PASSES` times."""
    ctx = engine.ctx()
    for _ in range(LIST_PASSES):
        after: UUID | None = None
        while True:
            started = time.perf_counter()
            page = await engine.managers.agent_sessions.get_agent_sessions(ctx, None, after, LIST_PAGE, Among.VISIBLE)
            into.latencies.append(time.perf_counter() - started)
            into.pages += 1
            into.items += len(page.items)
            if not page.has_more or not page.items:
                break
            after = page.items[-1].id


async def measured_read(engine: Engine, work: Callable[[Read], Coroutine[Any, Any, None]], *, profiled: bool) -> Read:
    """One read pass, timed, its key calls counted, and sampled. It runs as a
    task of a reader's own, in a context the sampler counts as working."""
    found = Read()
    engine.counts.keys.clear()
    token = SESSION.set(READER)
    try:
        task: asyncio.Task[None] = asyncio.create_task(work(found))
    finally:
        SESSION.reset(token)
    sampler = Sampler(RENDERING, for_a_session) if profiled else None
    if sampler is not None:
        sampler.start()
    started = time.perf_counter()
    await task
    found.seconds = time.perf_counter() - started
    if sampler is not None:
        found.profile = sampler.stop()
    found.key_calls = engine.counts.key_calls()
    return found


async def point(engine: Engine, scripts: list[list[Run]], *, profiled: bool) -> tuple[Point, list[UUID]]:
    """Every script replayed as one session, all at once; then each
    session's history read from its start and the session list read whole,
    as the API reads them."""
    counts = engine.counts
    counts.keys.clear()
    counts.appends.clear()
    counts.steps_written = 0
    counts.run_starts.clear()
    counts.running.clear()
    summaries = engine.twin.summaries
    sampler = Sampler(RENDERING, for_a_session) if profiled else None
    if sampler is not None:
        sampler.start()
    started = time.perf_counter()
    with timed_appends(counts):
        sessions = list(await asyncio.gather(*(replay(engine, runs) for runs in scripts)))
    seconds = time.perf_counter() - started
    profile = sampler.stop() if sampler is not None else None
    key_calls, keys = counts.key_calls(), dict(counts.keys)
    read: list[AgentStep] = []
    history = await measured_read(engine, lambda into: read_history(engine, sessions, into, read), profiled=profiled)
    listing = await measured_read(engine, lambda into: read_list(engine, into), profiled=profiled)
    return (
        Point(
            sessions=len(scripts),
            turns=sum(len(run.replies) for run in scripts[0]),
            seconds=seconds,
            steps_written=counts.steps_written,
            appends=list(counts.appends),
            key_calls=key_calls,
            keys=keys,
            history=history,
            listing=listing,
            said_bytes=sum(said_bytes(step) for step in read),
            kept_chars=sum(kept_chars(step) for step in read),
            summaries=engine.twin.summaries - summaries,
            profile=profile,
            run_starts=list(counts.run_starts),
        ),
        sessions,
    )
