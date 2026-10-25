"""The README's tables, rendered from one results file.

`render` turns a results file into the Markdown between the README's two
markers; `python -m replay --report <results.json>` writes it there, and
the root tests hold the README to what `render` makes of the file it
cites. It imports nothing from the engine."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

BEGIN = "<!-- results: begin -->"
END = "<!-- results: end -->"
CATEGORIES = (
    "seal and open",
    "key service",
    "database",
    "object store",
    "request rendering",
    "harness",
    "event loop",
    "rest",
)
"""The columns of the wall-time table; `idle`, the loop waiting with no
session's task waiting, is counted with the rest."""
KEYS = {"kms": "KMS emulator", "memory": "key twin"}
CITED = re.compile(r"\(results/([^)]+\.json)\)")


def _pct(share: float) -> str:
    return f"{share * 100:.1f}%"


def _shares(run: dict[str, Any]) -> dict[str, float]:
    shares = dict(run["profile"]["shares"])
    shares["rest"] = shares.get("rest", 0.0) + shares.pop("idle", 0.0)
    return shares


def _runs(results: dict[str, Any]) -> list[str]:
    lines = [
        "| keys | run | sessions | turns each | steps written | steps/s | append p50 | p95 | p99 "
        "| key calls / step written | key calls / step read | transactions / step | waiting |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for run in results["runs"]:
        ms = run["append_ms"]
        waiting = "" if run["profile"] is None else _pct(run["profile"]["waiting_share"])
        lines.append(
            f"| {KEYS[run['keys']]} | {run['name']} | {run['sessions']} | {run['turns_per_session']} "
            f"| {run['steps_written']} | {run['steps_per_second']} | {ms['p50']} ms | {ms['p95']} ms | {ms['p99']} ms "
            f"| {run['key_calls_per_step_written']} | {run['read']['key_calls_per_step_read']} "
            f"| {run['transactions_per_step_written']} | {waiting} |"
        )
    return lines


def _wall(results: dict[str, Any]) -> list[str]:
    lines = [
        "| keys | run | " + " | ".join(CATEGORIES) + " |",
        "| --- | --- | " + " | ".join("---:" for _ in CATEGORIES) + " |",
    ]
    for run in results["runs"]:
        if run["profile"] is None:
            continue
        shares = _shares(run)
        cells = " | ".join(_pct(shares.get(category, 0.0)) for category in CATEGORIES)
        lines.append(f"| {KEYS[run['keys']]} | {run['name']} | {cells} |")
    return lines


READS = (("history", "history, 50 steps a page"), ("list", "session list, 50 a page"))


def _reads(results: dict[str, Any]) -> list[str]:
    lines = [
        "| keys | run | read | pages | items / page | pages/s | page p50 | p95 | p99 | key calls / page "
        "| open | key service | database | rest |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for run in results["runs"]:
        for key, name in READS:
            read = run["read"][key]
            ms = read["page_ms"]
            shares = _shares(read) if read["profile"] else {}
            cells = " | ".join(
                _pct(shares.get(c, 0.0)) if shares else "" for c in ("seal and open", "key service", "database", "rest")
            )
            lines.append(
                f"| {KEYS[run['keys']]} | {run['name']} | {name} | {read['pages']} | {read['items_per_page']} "
                f"| {read['pages_per_second']} | {ms['p50']} ms | {ms['p95']} ms | {ms['p99']} ms "
                f"| {read['key_calls_per_page']} | {cells} |"
            )
    return lines


DRIVER = "performance/replay/__main__.py:main"
"""The bottom of every stack. A sample whose innermost engine frame is the
driver's is the event loop's own work: its scheduling, or a driver's
callback that no engine function is above, such as a socket's read."""


def _spender(name: str) -> str:
    return "the loop's own callbacks" if name == DRIVER else f"`{name}`"


def _ranked(run: dict[str, Any]) -> list[str]:
    shares = _shares(run)
    waited = run["profile"]["waiting_shares"]
    by = run["profile"]["by_category"]
    lines = [
        "| rank | where | share of wall time | of it, waiting | the code that spends it |",
        "| ---: | --- | ---: | ---: | --- |",
    ]
    ranked = sorted((c for c in shares if c in CATEGORIES and shares[c] > 0), key=lambda c: -shares[c])
    for rank, category in enumerate(ranked, start=1):
        spenders = by.get(category, []) + (by.get("idle", []) if category == "rest" else [])
        named = "<br>".join(f"{_spender(name)} {_pct(share)}" for name, share in spenders[:3])
        wait = waited.get(category, 0.0) + (waited.get("idle", 0.0) if category == "rest" else 0.0)
        lines.append(f"| {rank} | {category} | {_pct(shares[category])} | {_pct(wait)} | {named} |")
    return lines


def _at_rest(results: dict[str, Any]) -> list[str]:
    lines = [
        "| keys | run | bytes said | content at rest | per byte said | tool output text | objects | per byte kept |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for run in results["runs"]:
        if run["name"] != "alone":
            continue
        scan = run["scan_after"]
        said, kept = run["said_bytes"], run["kept_chars"]
        lines.append(
            f"| {KEYS[run['keys']]} | {run['name']} | {said} | {scan['content_bytes']} "
            f"| {scan['content_bytes'] / max(1, said):.3f} | {kept} | {scan['object_bytes']} "
            f"| {scan['object_bytes'] / max(1, kept):.3f} |"
        )
    return lines


def _starts(results: dict[str, Any]) -> list[str]:
    lines = [
        "| keys | run | runs | run start p50 | p95 | p99 | the last |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for run in results["runs"]:
        starts = run.get("run_start_ms")
        if starts is None:
            continue
        lines.append(
            f"| {KEYS[run['keys']]} | {run['name']} | {starts['runs']} | {starts['p50']} ms | {starts['p95']} ms "
            f"| {starts['p99']} ms | {starts['last']} ms |"
        )
    return lines


def _calibration(results: dict[str, Any]) -> list[str]:
    lines = ["| keys | a database round trip | an unwrap |", "| --- | ---: | ---: |"]
    seen: set[str] = set()
    for run in results["runs"]:
        if run["keys"] in seen:
            continue
        seen.add(run["keys"])
        c = run["calibration"]
        lines.append(f"| {KEYS[run['keys']]} | {c['database_round_trip_ms']} ms | {c['key_unwrap_ms']} ms |")
    return lines


def render(results: dict[str, Any], cited: str) -> str:
    m = results["machine"]
    widest = max((r for r in results["runs"] if r["keys"] == "kms" and r["profile"]), key=lambda r: r["sessions"])
    alone = {r["keys"]: r for r in results["runs"] if r["name"] == "alone"}
    parts = [
        f"From [`results/{cited}`](results/{cited}), started {results['started']}: the shape "
        f"`{results['shape']}` ({results['shape_turns']} turns) replayed for {results['turns']} turns alone, "
        f"and for {results['sweep_turns']} turns in each session of the sweep, on {m['processor']} "
        f"({m['cores']} cores, {m['memory_gb']} GB), {m['platform']}, Python {m['python']}.",
        "",
        "### The runs",
        "",
        *_runs(results),
        "",
        "### Where the wall time goes",
        "",
        *_wall(results),
        "",
        "### The reads, as the API pages them",
        "",
        "Open is the seal and open category: AES-GCM, the JSON, and the step's model, less its Python validators.",
        "",
        *_reads(results),
        "",
    ]
    for keys in ("kms", "memory"):
        if keys in alone:
            parts += [f"### Ranked: one session, {KEYS[keys]}", "", *_ranked(alone[keys]), ""]
    parts += [
        f"### Ranked: {widest['sessions']} sessions at once, {KEYS['kms']}",
        "",
        *_ranked(widest),
        "",
        "### Bytes at rest",
        "",
        *_at_rest(results),
        "",
    ]
    if any("run_start_ms" in run for run in results["runs"]):
        parts += [
            "### A run's start",
            "",
            "From the loop's run to its first model call, over every run of the point; the last is the longest history's.",
            "",
            *_starts(results),
            "",
        ]
    parts += [
        "### One round trip on this stack",
        "",
        *_calibration(results),
    ]
    return "\n".join(parts)


def block(readme: str) -> tuple[str, str]:
    """The README's generated block, and the results file it cites."""
    start, end = readme.index(BEGIN) + len(BEGIN), readme.index(END)
    inside = readme[start:end].strip("\n")
    found = CITED.search(inside)
    if found is None:
        raise ValueError("the README's results block cites no results file")
    return inside, found.group(1)


def write(readme_path: Path, results_path: Path) -> None:
    readme = readme_path.read_text(encoding="utf-8")
    start, end = readme.index(BEGIN) + len(BEGIN), readme.index(END)
    rendered = render(json.loads(results_path.read_text(encoding="utf-8")), results_path.name)
    readme_path.write_text(readme[:start] + "\n" + rendered + "\n" + readme[end:], encoding="utf-8")
