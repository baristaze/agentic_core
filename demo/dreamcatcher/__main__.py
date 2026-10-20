"""dreamcatcher: the engine in process, its run scripted end to end.

    python -m dreamcatcher            the screen, paced for a person
    python -m dreamcatcher --check    headless and fast: each act and the
                                      ledger checked against the record

The engine runs on its memory storage, with the scripted twin as every
model provider: no key is read, no connection opened, nothing spent.
"""

import argparse
import asyncio
import dataclasses
import os
import shutil
import sys
import tempfile
from pathlib import Path

from dreamcatcher import checks
from dreamcatcher.acts import Act, Director, Miscue, Proof, Script, read_script
from dreamcatcher.engine import Engine
from dreamcatcher.projection import project, until
from dreamcatcher.screen import ACCENT, ANSI, BRIGHT, DIM, HEIGHT, TEXT, WIDTH, compose, paint
from dreamcatcher.workspace import Workspace

SCRIPT = Path(__file__).with_name("script.json")
SLOTS = 72
"""The ring's slots: past them, each new step narrows every slice."""
KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "ACME_ANTHROPIC_API_KEY", "ACME_OPENAI_API_KEY")
"""The names a provider's key goes by in a shell, and in the engine's settings."""


@dataclasses.dataclass(frozen=True)
class Pace:
    """Seconds, for a person to follow: each streamed part, each tool call,
    each beat the director narrates, each act's title, and the replay."""

    part: float = 0.05
    tool: float = 0.3
    beat: float = 2.6
    title: float = 2.4
    replay: float = 4.0
    close: float = 6.0

    def scaled(self, factor: float) -> "Pace":
        return Pace(*(getattr(self, f.name) * factor for f in dataclasses.fields(self)))


class Show:
    """The screen, redrawn from the record while the director plays."""

    def __init__(self, engine: Engine, script: Script, pace: Pace) -> None:
        self.engine = engine
        self.pace = pace
        self.director = Director(engine, script, self.narrate, self.announce)
        self.caption = paint("dreamcatcher", ACCENT, bold=True)
        self.beat = ""
        self.act = ""
        self.drawn = ""

    def header(self) -> str:
        title = paint(" dreamcatcher ", BRIGHT, bold=True)
        about = paint("· the engine in process · memory storage · the scripted twin as its model · no network", DIM)
        return f"{title}{about}{paint(self.act.rjust(WIDTH - 101), TEXT)}"

    async def narrate(self, text: str) -> None:
        self.beat = paint("▸ ", ACCENT) + paint(text, TEXT)
        await asyncio.sleep(self.pace.beat)

    async def announce(self, act: Act) -> None:
        self.act = act.title.split(" · ")[0]
        self.caption = paint(act.title, ACCENT, bold=True) + paint(f"   {act.about}", TEXT)
        self.beat = ""
        await asyncio.sleep(self.pace.title)

    def draw(self, text: str) -> None:
        if text != self.drawn:
            sys.stdout.write(text)
            sys.stdout.flush()
            self.drawn = text

    async def paint_while(self, playing: "asyncio.Task[Proof]") -> None:
        while not playing.done():
            root = self.director.proof.root
            if root is not None:
                frame = project(await self.engine.read(root))
                self.draw(compose(frame, SLOTS, self.header(), self.caption, self.beat))
            await asyncio.sleep(1 / 15)

    async def run(self) -> None:
        playing = asyncio.ensure_future(self.director.play())
        await asyncio.gather(playing, self.paint_while(playing))
        root = self.director.proof.root
        assert root is not None
        record = await self.engine.read(root)
        closing = project(record)
        self.caption = paint("The replay", ACCENT, bold=True) + paint("   the same ring, redrawn from the history alone", TEXT)
        self.beat = ""
        count = len(record.steps[root])
        for n in range(1, count + 1):
            frame = dataclasses.replace(project(until(record, n)), ledger=closing.ledger)
            self.draw(compose(frame, SLOTS, self.header(), self.caption, self.beat))
            await asyncio.sleep(self.pace.replay / count)
        self.caption = paint("The ledger closes", ACCENT, bold=True) + paint(
            "   every number above is a query over the history and the usage records", TEXT
        )
        spent = closing.ledger.cost_micros / 1_000_000
        self.beat = paint("▸ ", ACCENT) + paint(
            f"${spent:.4f} at list price for the scripted usage, the sum of its usage records; nothing was billed", TEXT
        )
        self.draw(compose(closing, SLOTS, self.header(), self.caption, self.beat))
        await asyncio.sleep(self.pace.close)


async def show(pace: Pace) -> int:
    size = shutil.get_terminal_size()
    if size.columns < WIDTH or size.lines < HEIGHT:
        print(f"dreamcatcher draws {WIDTH} by {HEIGHT}; this terminal is {size.columns} by {size.lines}.")
        return 2
    sys.stdout.write("\x1b[?1049h\x1b[?25l\x1b[2J")
    try:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Workspace(pause=pace.tool)
            engine = Engine(Path(folder), pace.part, workspace)
            await Show(engine, read_script(SCRIPT), pace).run()
    finally:
        sys.stdout.write("\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()
    return 0


def say(text: str) -> None:
    """A line of the check's report, in color on a terminal alone."""
    print(text if sys.stdout.isatty() else ANSI.sub("", text))


def report(title: str, claims: list[checks.Claim]) -> bool:
    say(paint(title, ACCENT, bold=True))
    for claim, held in claims:
        mark = paint("✓", 114) if held else paint("✗", 203)
        say(f"  {mark} {claim}")
    return all(held for _, held in claims)


async def check() -> int:
    """The run headless, with every outbound connection refused and no
    paid provider buildable; then each act and the ledger read back."""
    keyed = all(name in os.environ for name in KEYS)
    checks.refuse_outbound()
    checks.refuse_paid_providers()
    probe = checks.outbound_refused()
    script = read_script(SCRIPT)
    with tempfile.TemporaryDirectory() as folder:
        engine = Engine(Path(folder), 0.0, Workspace())

        async def quiet(_: object) -> None:
            return None

        director = Director(engine, script, quiet, quiet)
        try:
            proof = await director.play()
        except Miscue as missed:
            say(paint(f"failed: {missed}", 203, bold=True))
            return 1
        assert proof.root is not None
        record = await engine.read(proof.root)
        acted = await checks.acts(engine, script, proof, record)
        closed = await checks.ledger(engine, record)
        led = project(record).ledger
    ok = report("offline", checks.offline(engine, keyed, probe))
    ok = report("the acts, from the history", acted) and ok
    say(paint("the closing ledger", ACCENT, bold=True))
    width = max(len(t) for t, _ in led.steps)
    for kind, count in led.steps:
        print(f"  {kind:<{width}} {count:>5}")
    print(f"  {'model calls':<{width}} {led.model_calls:>5}")
    print(f"  {'tokens':<{width}} in {led.tokens_in:,} · out {led.tokens_out:,} · cache read {led.tokens_cache:,}")
    print(f"  {'cost':<{width}} ${led.cost_micros / 1_000_000:.6f} of a ${(led.cap_micros or 0) / 1_000_000:.2f} cap")
    ok = report("the ledger equals the record read back", closed) and ok
    say(paint("ok" if ok else "failed", 114 if ok else 203, bold=True))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dreamcatcher", description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="run headless and check each act and the ledger")
    parser.add_argument("--pace", type=float, default=1.0, help="stretch or shrink every pause (default 1)")
    args = parser.parse_args(argv)
    if args.check:
        return asyncio.run(check())
    return asyncio.run(show(Pace().scaled(args.pace)))


if __name__ == "__main__":
    sys.exit(main())
