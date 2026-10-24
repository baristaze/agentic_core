"""Where the wall time goes: a sampling profile of the event loop's thread,
attributed to the engine's functions.

A thread wakes every `interval` and reads the stack of the thread that runs
the event loop. When the loop runs code, the sample is that code's. When the
loop waits with nothing to run, the sample is split between the tasks that
wait, each by the coroutine it waits in: a query waits in the database, an
unwrap in the key service. Each sample weighs the wall time since the one
before it, so the shares add up to the wall time measured.

A sample is given the category of the first frame, from the innermost out,
that a rule matches, or that is the engine's or the harness's: a key
service's call made to seal a step is the key service's, a key ring read is
the database's, and the engine's own code that no rule names is the rest. Each sample also counts
toward every engine function on its stack, for the inclusive table, and
toward the innermost engine function, for the self table."""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from types import CodeType, FrameType
from typing import Any

CATEGORIES: tuple[tuple[str, tuple[str, ...], frozenset[str] | None], ...] = (
    # (category, the path parts a frame's file holds, the functions, or every one)
    # The harness: the twin and its parts, the synthetic tools and their
    # words, and the counting around the key service and the appends. The
    # driver that starts the sessions sits at the bottom of every stack and
    # takes no sample of its own, so it is none of it.
    (
        "harness",
        (
            "/performance/replay/engine.py",
            "/performance/replay/tools.py",
            "/performance/replay/words.py",
            "/performance/replay/generate.py",
        ),
        None,
    ),
    ("harness", ("/acme/integrations/model_providers/scripted.py",), None),
    (
        "seal and open",
        ("/acme/om/privacy/impl/sealed_agent_steps.py",),
        frozenset({"sealed", "opened", "absent", "refuse_sealed", "says_something", "bound_to"}),
    ),
    (
        "seal and open",
        ("/acme/om/privacy/impl/blobs.py", "/acme/om/privacy/impl/tool_outputs.py", "/acme/om/privacy/impl/records.py"),
        None,
    ),
    ("seal and open", ("/acme/om/privacy/impl/manager.py",), frozenset({"keyed_hash"})),
    ("key service", ("/acme/infra/keys/",), None),
    ("object store", ("/acme/infra/buckets/",), None),
    # The loop's own work between tasks: a frame of its scheduling, with no
    # frame of the engine's inside it.
    ("event loop", ("/asyncio/base_events.py", "/asyncio/events.py"), None),
    ("database", ("/asyncpg/", "/sqlalchemy/", "/storage/impl/postgres", "/storage/impl/pg_base", "/storage/impl/memory"), None),
)
"""The rules a sample's frames are matched against. The request rendering
rules are the caller's, since they name the engine's functions that turn a
history into a model call."""


IDLE_FILES = ("/selectors.py", "/asyncio/base_events.py")
ENGINE = "/acme/"


def ranked(seconds: dict[str, float], top: int | None = None) -> list[tuple[str, float]]:
    return sorted(seconds.items(), key=lambda kv: -kv[1])[:top]


def qualname(code: CodeType) -> str:
    """The function's qualified name, `Class.method`, where the Python has it."""
    return str(getattr(code, "co_qualname", code.co_name))


@dataclass
class Profile:
    """What the sampler saw: wall seconds by category, by the innermost engine
    function (self), and by every engine function on the stack (inclusive)."""

    seconds: defaultdict[str, float] = field(default_factory=lambda: defaultdict(float))
    self_seconds: defaultdict[str, float] = field(default_factory=lambda: defaultdict(float))
    by_category: dict[str, defaultdict[str, float]] = field(default_factory=dict)
    waited: defaultdict[str, float] = field(default_factory=lambda: defaultdict(float))
    """Of each category's seconds, those the loop spent waiting on it."""
    inclusive_seconds: defaultdict[str, float] = field(default_factory=lambda: defaultdict(float))
    idle_unattributed: float = 0.0
    waiting: float = 0.0
    samples: int = 0
    wall: float = 0.0

    def as_dict(self, top: int = 25) -> dict[str, Any]:
        total = sum(self.seconds.values()) or 1.0
        return {
            "samples": self.samples,
            "wall_seconds": round(self.wall, 3),
            "waiting_share": round(self.waiting / total, 4),
            "shares": {k: round(v / total, 4) for k, v in ranked(self.seconds)},
            "waiting_shares": {k: round(self.waited[k] / total, 4) for k, _ in ranked(self.seconds)},
            "self": [[k, round(v / total, 4)] for k, v in ranked(self.self_seconds, top)],
            "by_category": {
                category: [[k, round(v / total, 4)] for k, v in ranked(counter, 8)]
                for category, counter in sorted(self.by_category.items(), key=lambda kv: -self.seconds[kv[0]])
            },
            "inclusive": [[k, round(v / total, 4)] for k, v in ranked(self.inclusive_seconds, top)],
        }


class Sampler:
    def __init__(
        self,
        rendering: dict[str, frozenset[str] | None],
        working: Callable[[asyncio.Task[Any]], bool],
        interval: float = 0.002,
    ) -> None:
        self._working = working
        self._rules = [*CATEGORIES, *(("request rendering", (path,), names) for path, names in rendering.items())]
        self._interval = interval
        self._target = threading.get_ident()
        self._loop = asyncio.get_running_loop()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="performance-sampler", daemon=True)
        self._cache: dict[CodeType, tuple[str | None, str | None]] = {}
        self.profile = Profile()

    def start(self) -> None:
        self._started = time.perf_counter()
        self._thread.start()

    def stop(self) -> Profile:
        self._stop.set()
        self._thread.join()
        self.profile.wall = time.perf_counter() - self._started
        return self.profile

    def _run(self) -> None:
        last = time.perf_counter()
        while not self._stop.wait(self._interval):
            now = time.perf_counter()
            weight, last = now - last, now
            frame = sys._current_frames().get(self._target)
            if frame is None:
                continue
            self.profile.samples += 1
            codes = _stack(frame)
            if _idle(codes):
                self._waiting(weight)
            else:
                self._count(codes, weight)

    def _waiting(self, weight: float) -> None:
        """The loop waits: the weight is split between the tasks that work for
        a replayed session, each by where it waits. A task of the process's
        own, such as a subscriber that waits on its channel for the life of
        the process, is none of them."""
        try:
            tasks = list(asyncio.all_tasks(self._loop))
        except RuntimeError:
            tasks = []
        chains = [_chain(task) for task in tasks if self._working(task)]
        if not chains:
            self.profile.idle_unattributed += weight
            self.profile.seconds["idle"] += weight
            self.profile.waited["idle"] += weight
            return
        self.profile.waiting += weight
        share = weight / len(chains)
        for codes in chains:
            self._count(codes, share, waiting=True)

    def _count(self, codes: list[CodeType], weight: float, *, waiting: bool = False) -> None:
        """`codes` runs from the innermost frame out. The first frame that a
        rule matches gives the category, unless a frame of the engine or the
        harness that no rule matches comes first: its own code is the rest."""
        category = "rest"
        for code in codes:
            found, label = self._named(code)
            if found is not None:
                category = found
                break
            if label is not None:
                break
        self.profile.seconds[category] += weight
        if waiting:
            self.profile.waited[category] += weight
        seen: set[str] = set()
        innermost = True
        for code in codes:
            name = self._named(code)[1]
            if name is None:
                continue
            if innermost:
                self.profile.self_seconds[name] += weight
                self.profile.by_category.setdefault(category, defaultdict(float))[name] += weight
                innermost = False
            if name not in seen:
                seen.add(name)
                self.profile.inclusive_seconds[name] += weight

    def _named(self, code: CodeType) -> tuple[str | None, str | None]:
        """The code's category, if a rule matches it, and its engine name,
        `<file under acme or performance>:<function>`, if it is the engine's
        or the harness's."""
        hit = self._cache.get(code)
        if hit is not None:
            return hit
        path = code.co_filename
        category = None
        for name, parts, functions in self._rules:
            if any(part in path for part in parts) and (functions is None or code.co_name in functions):
                category = name
                break
        label = None
        for root in (ENGINE, "/performance/"):
            at = path.rfind(root)
            if at >= 0:
                label = f"{path[at + 1 :]}:{qualname(code)}"
                break
        self._cache[code] = (category, label)
        return category, label


def _stack(frame: FrameType | None) -> list[CodeType]:
    codes: list[CodeType] = []
    while frame is not None:
        codes.append(frame.f_code)
        frame = frame.f_back
    return codes


def _idle(codes: list[CodeType]) -> bool:
    """The loop's thread waits in its selector, with nothing to run."""
    return (
        bool(codes)
        and codes[0].co_name in {"select", "control", "poll"}
        and any(part in codes[0].co_filename for part in IDLE_FILES)
    )


def _chain(task: asyncio.Task[Any]) -> list[CodeType]:
    """The coroutines a task waits through, from the innermost out."""
    codes: list[CodeType] = []
    awaited: Any = task.get_coro()
    for _ in range(256):
        frame = getattr(awaited, "cr_frame", None) or getattr(awaited, "ag_frame", None) or getattr(awaited, "gi_frame", None)
        if frame is None:
            break
        codes.append(frame.f_code)
        awaited = (
            getattr(awaited, "cr_await", None) or getattr(awaited, "ag_await", None) or getattr(awaited, "gi_yieldfrom", None)
        )
        if awaited is None:
            break
    codes.reverse()
    return codes
