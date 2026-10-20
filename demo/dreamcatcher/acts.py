"""The director: the person, and the runners, in three acts.

The script holds the words: the model's turns and what the person says.
This module holds the cues: when a run starts, when the person speaks,
when a runner is lost and another takes over. Each act leaves its proof
in `Proof`, the ids a check reads the history by, and when each run of
the root began, so the tape can say at which epoch each step was written.
"""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from acme.integrations.model_providers.scripted import SCRIPT, Turn
from acme.integrations.model_providers.types import ProviderName
from acme.om.agents.types.run import LoopRun
from acme.om.base import utcnow
from acme.om.spend_budgets.types.spend_budget import SpendBudget
from dreamcatcher.engine import FIXER, Engine


@dataclass(frozen=True)
class Act:
    name: str
    title: str
    about: str
    person: dict[str, str]
    turns: dict[ProviderName, list[Turn]]
    """The act's model turns, as the scripted twin's own file holds them."""


@dataclass(frozen=True)
class Script:
    budget_micros: int
    raised_micros: int
    acts: list[Act]


def read_script(path: Path) -> Script:
    raw: dict[str, Any] = json.loads(path.read_text())
    acts = [Act(a["name"], a["title"], a["about"], dict(a["person"]), SCRIPT.validate_python(a["turns"])) for a in raw["acts"]]
    return Script(int(raw["budget_micros"]), int(raw["raised_micros"]), acts)


@dataclass
class Proof:
    """What each act did, by id, so a check can find it in the history."""

    root: UUID | None = None
    acts: dict[str, int] = field(default_factory=dict)
    """The seq of each act's first message."""
    steer: UUID | None = None
    answer: UUID | None = None
    runs: dict[str, LoopRun] = field(default_factory=dict)
    begun: dict[str, datetime] = field(default_factory=dict)
    """When each run of the root began, on the clock the engine stamps its
    steps with: a step written after it, and before the next run began,
    is written at the epoch that run holds."""
    budget: SpendBudget | None = None
    """The budget as it was set, before any raise."""


CUE = 30.0
"""Seconds a cue may take to come: past it, the run no longer plays the
script, and the director says which cue it missed."""


class Miscue(Exception):
    """A cue the run never reached: the script and the engine disagree."""


async def reached(event: asyncio.Event, cue: str) -> None:
    try:
        await asyncio.wait_for(event.wait(), CUE)
    except asyncio.TimeoutError:
        raise Miscue(cue) from None


class Director:
    def __init__(self, engine: Engine, script: Script) -> None:
        self.engine = engine
        self.script = script
        self.proof = Proof()

    async def play(self) -> Proof:
        self.proof.root = await self.engine.start(FIXER.name, "fix the failing check")
        self.proof.budget = await self.engine.cap(self.proof.root, self.script.budget_micros)
        simple, smart, power = self.script.acts
        await self.simple(simple)
        await self.smart(smart)
        await self.power(power)
        return self.proof

    @property
    def root(self) -> UUID:
        assert self.proof.root is not None
        return self.proof.root

    def begin(self, name: str) -> "asyncio.Future[LoopRun]":
        """A run of the root, begun, and when it began."""
        self.proof.begun[name] = utcnow()
        return asyncio.ensure_future(self.engine.run(self.root))

    async def run(self, name: str) -> LoopRun:
        self.proof.runs[name] = run = await self.begin(name)
        return run

    async def _open(self, act: Act) -> None:
        self.engine.script(act.turns)
        asked = await self.engine.say(self.root, act.person["ask"])
        self.proof.acts[act.name] = asked.seq

    async def simple(self, act: Act) -> None:
        await self._open(act)
        await self.run("simple")

    async def smart(self, act: Act) -> None:
        await self._open(act)
        check = self.engine.workspace.cue("run_check")
        running = self.begin("asked")
        await reached(check.started, "act 2: the run never reached the check the steer lands in")
        self.proof.steer = (await self.engine.say(self.root, act.person["steer"])).id
        check.release.set()
        self.proof.runs["asked"] = await running
        self.proof.answer = (await self.engine.say(self.root, act.person["answer"])).id
        await self.run("waiting")
        for n, child in enumerate(await self.engine.children(self.root), start=1):
            self.proof.runs[f"child {n}"] = await self.engine.run(child.id)
        await self.run("smart")

    async def power(self, act: Act) -> None:
        await self._open(act)
        capped = await self.run("capped")
        if capped.park is None:
            raise Miscue("act 3: the budget no longer parks the act's first call; tune budget_micros")
        await self.engine.raise_budget(self.script.raised_micros)
        stall = self.engine.workspace.cue("edit_file")
        lost = self.begin("lost")
        await reached(stall.started, "act 3: the run never reached the edit its runner dies in")
        await self.run("power")
        stall.release.set()
        self.proof.runs["lost"] = await lost
