"""The performance run.

    python -m replay --check                 # a short replay on memory and the key twin, checked
    python -m replay --full <results.json>   # the whole run on the stack stack.env names
    python -m replay --point kms 1 1500      # one point: key service, sessions, turns
    python -m replay --report <results.json> # the README's tables, from a results file

The shape is `shapes/long-session.json` unless `--shape` names another."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

from replay.checks import check_memory
from replay.full import full, one_point
from replay.report import write
from replay.shape import load

HERE = Path(__file__).resolve().parent.parent
SHAPE = HERE / "shapes" / "long-session.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="replay", description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--full", type=Path, metavar="RESULTS")
    mode.add_argument("--point", nargs=3, metavar=("KEYS", "SESSIONS", "TURNS"))
    mode.add_argument("--report", type=Path, metavar="RESULTS")
    parser.add_argument("--shape", type=Path, default=SHAPE)
    parser.add_argument("--turns", type=int, default=1500)
    parser.add_argument("--sweep", default="1,2,4,8,16,32")
    parser.add_argument("--sweep-turns", type=int, default=150, help="turns each session of the sweep replays")
    parser.add_argument("--profile", action="store_true", help="with --point: sample where the time goes")
    args = parser.parse_args(argv)
    if args.report:
        write(HERE / "README.md", args.report)
        return 0
    shape = load(args.shape)
    if args.check:
        with tempfile.TemporaryDirectory(prefix="performance-check-") as folder:
            return asyncio.run(check_memory(shape, Path(folder)))
    if args.point:
        keys, sessions, turns = args.point
        result = asyncio.run(one_point(shape, keys, int(sessions), int(turns), profiled=args.profile))
        print(json.dumps(result, indent=1))
        return 0
    sweep = tuple(int(n) for n in args.sweep.split(","))
    return asyncio.run(full(shape, args.shape.name, args.full, args.turns, sweep, args.sweep_turns))


if __name__ == "__main__":
    sys.exit(main())
