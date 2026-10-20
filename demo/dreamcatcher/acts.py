"""The director: the person, and the runners, in three acts.

The script holds the words: the model's turns and what the person says.
This module holds the cues: when a run starts, when the person speaks,
when a runner is lost and another takes over. Each act leaves its proof
in `Proof`, the ids a check reads the history by.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from acme.integrations.model_providers.scripted import SCRIPT, Turn
from acme.integrations.model_providers.types import ProviderName
from acme.om.agents.types.run import LoopRun
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


Say = Callable[[str], Awaitable[None]]
"""How the director narrates a beat: the screen shows it, and waits."""

Announce = Callable[[Act], Awaitable[None]]
"""How the director opens an act."""


class Director:
    def __init__(self, engine: Engine, script: Script, narrate: Say, announce: Announce) -> None:
        self.engine = engine
        self.script = script
        self.narrate = narrate
        self.announce = announce
        self.proof = Proof()

    async def play(self) -> Proof:
        simple, smart, power = self.script.acts
        for act, play in ((simple, self.simple), (smart, self.smart), (power, self.power)):
            await self.announce(act)
            await play(act)
        return self.proof

    async def _open(self, act: Act) -> UUID:
        root = self.proof.root
        assert root is not None
        self.engine.script(act.turns)
        await self.narrate(f"the person: “{act.person['ask']}”")
        asked = await self.engine.say(root, act.person["ask"])
        self.proof.acts[act.name] = asked.seq
        return root

    async def simple(self, act: Act) -> None:
        self.proof.root = await self.engine.start(FIXER.name, "fix the failing check")
        await self.engine.cap(self.proof.root, self.script.budget_micros)
        root = await self._open(act)
        self.proof.runs["simple"] = await self.engine.run(root)

    async def smart(self, act: Act) -> None:
        root = await self._open(act)
        check = self.engine.workspace.cue("run_check")
        running = asyncio.ensure_future(self.engine.run(root))
        await reached(check.started, "act 2: the run never reached the check the steer lands in")
        await self.narrate(f"mid tool call, the person steers: “{act.person['steer']}”")
        self.proof.steer = (await self.engine.say(root, act.person["steer"])).id
        check.release.set()
        self.proof.runs["asked"] = await running
        await self.narrate(f"parked on person; the person answers: “{act.person['answer']}”")
        self.proof.answer = (await self.engine.say(root, act.person["answer"])).id
        self.proof.runs["waiting"] = await self.engine.run(root)
        for n, child in enumerate(await self.engine.children(root), start=1):
            await self.narrate(f"sub-agent {n} of 2 runs: {child.title}")
            self.proof.runs[f"child {n}"] = await self.engine.run(child.id)
        await self.narrate("both reports reached the parent; it reads them")
        self.proof.runs["smart"] = await self.engine.run(root)

    async def power(self, act: Act) -> None:
        root = await self._open(act)
        self.proof.runs["capped"] = capped = await self.engine.run(root)
        if capped.park is None:
            raise Miscue("act 3: the budget no longer parks the act's first call; tune budget_micros")
        raised = self.script.raised_micros / 1_000_000
        await self.narrate(f"parked on spend_budget before the call; the owner raises the cap to ${raised:.2f}")
        await self.engine.raise_budget(self.script.raised_micros)
        stall = self.engine.workspace.cue("edit_file")
        lost = asyncio.ensure_future(self.engine.run(root))
        await reached(stall.started, "act 3: the run never reached the edit its runner dies in")
        await self.narrate("runner A dies mid edit_file; runner B takes the session over")
        self.proof.runs["power"] = await self.engine.run(root)
        await self.narrate("runner A wakes and writes")
        stall.release.set()
        self.proof.runs["lost"] = await lost
