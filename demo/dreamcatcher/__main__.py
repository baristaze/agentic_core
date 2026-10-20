"""dreamcatcher: the engine in process, its run scripted end to end.

    python -m dreamcatcher            the run, written to the scene's tape,
                                      and the scene opened in a browser
    python -m dreamcatcher --tape     the run, written to the tape alone
    python -m dreamcatcher --check    headless: each act, the ledger, and
                                      the tape checked against the record

The engine runs on its memory storage, with the scripted twin as every
model provider: no key is read, no connection opened, nothing spent.
"""

import argparse
import asyncio
import os
import re
import sys
import tempfile
import webbrowser
from pathlib import Path

from dreamcatcher import checks, tape
from dreamcatcher.acts import Director, Miscue, Proof, Script, read_script
from dreamcatcher.engine import Engine, Record
from dreamcatcher.projection import project
from dreamcatcher.workspace import Workspace

SCRIPT = Path(__file__).with_name("script.json")
PAGE = tape.TAPE.with_name("index.html")
KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "ACME_ANTHROPIC_API_KEY", "ACME_OPENAI_API_KEY")
"""The names a provider's key goes by in a shell, and in the engine's settings."""

ANSI = re.compile(r"\x1b\[[0-9;]*m")
ACCENT, GOOD, BAD = 81, 114, 203


def paint(text: str, color: int, bold: bool = False) -> str:
    return f"\x1b[{'1;' if bold else ''}38;5;{color}m{text}\x1b[0m"


def say(text: str) -> None:
    """A line of the report, in color on a terminal alone."""
    print(text if sys.stdout.isatty() else ANSI.sub("", text))


def report(title: str, claims: list[checks.Claim]) -> bool:
    say(paint(title, ACCENT, bold=True))
    for claim, held in claims:
        mark = paint("✓", GOOD) if held else paint("✗", BAD)
        say(f"  {mark} {claim}")
    return all(held for _, held in claims)


async def play(engine: Engine, script: Script) -> tuple[Proof, Record] | None:
    """The three acts, headless and at once; None when a cue never came."""
    try:
        proof = await Director(engine, script).play()
    except Miscue as missed:
        say(paint(f"failed: {missed}", BAD, bold=True))
        return None
    assert proof.root is not None
    return proof, await engine.read(proof.root)


async def record(open_page: bool) -> int:
    """The run, written to the tape the scene plays."""
    script = read_script(SCRIPT)
    with tempfile.TemporaryDirectory() as folder:
        played = await play(Engine(Path(folder), 0.0, Workspace()), script)
    if played is None:
        return 1
    proof, history = played
    tape.TAPE.write_text(tape.render(tape.write(history, proof, script)))
    say(f"wrote {tape.TAPE}")
    if open_page:
        webbrowser.open(PAGE.as_uri())
        say(f"opened {PAGE}")
    return 0


async def check() -> int:
    """The run headless, with every outbound connection refused and no
    paid provider buildable; then each act, the ledger, and the tape read
    back."""
    keyed = all(name in os.environ for name in KEYS)
    checks.refuse_outbound()
    checks.refuse_paid_providers()
    probe = checks.outbound_refused()
    script = read_script(SCRIPT)
    with tempfile.TemporaryDirectory() as folder:
        engine = Engine(Path(folder), 0.0, Workspace())
        played = await play(engine, script)
        if played is None:
            return 1
        proof, history = played
        acted = await checks.acts(engine, script, proof, history)
        closed = await checks.ledger(engine, history)
        written = tape.write(history, proof, script)
        taped = await checks.tape(engine, history, written, tape.TAPE)
        led = project(history)
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
    ok = report("the tape the scene plays", taped) and ok
    say(paint("ok" if ok else "failed", GOOD if ok else BAD, bold=True))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dreamcatcher", description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="run headless and check each act, the ledger, and the tape")
    mode.add_argument("--tape", action="store_true", help="write the tape, and open nothing")
    args = parser.parse_args(argv)
    if args.check:
        return asyncio.run(check())
    return asyncio.run(record(open_page=not args.tape))


if __name__ == "__main__":
    sys.exit(main())
