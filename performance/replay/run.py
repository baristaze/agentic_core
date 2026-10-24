"""One point of the run: a number of sessions, each replaying the shape's
turns at once in this process, on one engine. It measures the steps
written a second, each append's latency, the key service's calls for each
step written and each step read, and where the wall time goes."""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from collections import deque
from dataclasses import dataclass
from typing import Any
from uuid import UUID

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


@dataclass
class Point:
    sessions: int
    turns: int
    seconds: float
    steps_written: int
    appends: list[float]
    key_calls: int
    keys: dict[str, int]
    read_steps: int
    read_key_calls: int
    read_seconds: float
    said_bytes: int
    kept_chars: int
    summaries: int
    profile: Profile | None
    harness_seconds: float

    def as_dict(self) -> dict[str, Any]:
        cuts = statistics.quantiles(self.appends, n=100) if len(self.appends) > 1 else [0.0] * 99
        return {
            "sessions": self.sessions,
            "turns_per_session": self.turns,
            "wall_seconds": round(self.seconds, 3),
            "steps_written": self.steps_written,
            "steps_per_second": round(self.steps_written / self.seconds, 1),
            "appends": len(self.appends),
            "append_ms": {
                "p50": round(cuts[49] * 1000, 2),
                "p95": round(cuts[94] * 1000, 2),
                "p99": round(cuts[98] * 1000, 2),
            },
            "key_calls": self.key_calls,
            "key_calls_per_step_written": round(self.key_calls / max(1, self.steps_written), 3),
            "key_calls_by_asker": dict(sorted(self.keys.items(), key=lambda kv: -kv[1])),
            "read_back": {
                "steps": self.read_steps,
                "seconds": round(self.read_seconds, 3),
                "key_calls": self.read_key_calls,
                "key_calls_per_step_read": round(self.read_key_calls / max(1, self.read_steps), 4),
            },
            "said_bytes": self.said_bytes,
            "kept_chars": self.kept_chars,
            "summaries": self.summaries,
            "profile": None if self.profile is None else self.profile.as_dict(),
        }


async def point(engine: Engine, scripts: list[list[Run]], *, profiled: bool) -> tuple[Point, list[UUID]]:
    """Every script replayed as one session, all at once, and then each
    session's history read back whole."""
    counts = engine.counts
    counts.keys.clear()
    counts.appends.clear()
    counts.steps_written = 0
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
    counts.keys.clear()
    started = time.perf_counter()
    read = said = kept = 0
    for agent_session_id in sessions:
        history = await engine.history(agent_session_id)
        read += len(history)
        said += sum(said_bytes(step) for step in history)
        kept += sum(kept_chars(step) for step in history)
    read_seconds = time.perf_counter() - started
    harness = 0.0 if profile is None else profile.seconds.get("harness", 0.0)
    return (
        Point(
            sessions=len(scripts),
            turns=sum(len(run.replies) for run in scripts[0]),
            seconds=seconds,
            steps_written=counts.steps_written,
            appends=list(counts.appends),
            key_calls=key_calls,
            keys=keys,
            read_steps=read,
            read_key_calls=counts.key_calls(),
            read_seconds=read_seconds,
            said_bytes=said,
            kept_chars=kept,
            summaries=engine.twin.summaries - summaries,
            profile=profile,
            harness_seconds=harness,
        ),
        sessions,
    )
