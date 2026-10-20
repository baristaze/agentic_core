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
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import TypeAdapter

from acme.integrations.model_providers.scripted import Turn
from acme.om.agents.types.run import LoopRun

from dreamcatcher.engine import FIXER, Engine

TURNS = TypeAdapter(List[Turn])


@dataclass(frozen=True)
class Act:
    name: str
    title: str
    about: str
    person: Dict[str, str]
    turns: List[Turn]


@dataclass(frozen=True)
class Script:
    budget_micros: int
    raised_micros: int
    acts: List[Act]


def read_script(path: Path) -> Script:
    raw: Dict[str, Any] = json.loads(path.read_text())
    acts = [
        Act(a["name"], a["title"], a["about"], dict(a["person"]), TURNS.validate_python(a["turns"]))
        for a in raw["acts"]
    ]
    return Script(int(raw["budget_micros"]), int(raw["raised_micros"]), acts)


@dataclass
class Proof:
    """What each act did, by id, so a check can find it in the history."""

    root: Optional[UUID] = None
    acts: Dict[str, int] = field(default_factory=dict)
    """The seq of each act's first message."""
    steer: Optional[UUID] = None
    answer: Optional[UUID] = None
    runs: Dict[str, LoopRun] = field(default_factory=dict)


Say = Callable[[str], Awaitable[None]]
"""How the director narrates a beat: the screen shows it, and waits."""


class Director:
    def __init__(self, engine: Engine, script: Script, narrate: Say) -> None:
        self.engine = engine
        self.script = script
        self.narrate = narrate
        self.proof = Proof()

    async def play(self) -> Proof:
        await self.simple(self.script.acts[0])
        await self.smart(self.script.acts[1])
        await self.power(self.script.acts[2])
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
        await check.started.wait()
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
        self.proof.runs["capped"] = await self.engine.run(root)
        raised = self.script.raised_micros / 1_000_000
        await self.narrate(f"parked on spend_budget before the call; the owner raises the cap to ${raised:.2f}")
        await self.engine.raise_budget(self.script.raised_micros)
        stall = self.engine.workspace.cue("edit_file")
        lost = asyncio.ensure_future(self.engine.run(root))
        await stall.started.wait()
        await self.narrate("runner A dies mid edit_file; runner B takes the session over")
        self.proof.runs["power"] = await self.engine.run(root)
        await self.narrate("runner A wakes and writes")
        stall.release.set()
        self.proof.runs["lost"] = await lost
