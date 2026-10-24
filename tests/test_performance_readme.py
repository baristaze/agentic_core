"""`performance/README.md` shows the numbers of the results file it cites:
its generated block is what `replay.report.render` makes of that file, so
the ranking a reader sizes the next change from is the measured one."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PERFORMANCE = ROOT / "performance"
if str(PERFORMANCE) not in sys.path:
    sys.path.insert(0, str(PERFORMANCE))

from replay.report import block, render  # noqa: E402


def test_the_readme_tables_equal_the_results_file_they_cite() -> None:
    inside, cited = block((PERFORMANCE / "README.md").read_text(encoding="utf-8"))
    results = PERFORMANCE / "results" / cited
    assert results.is_file(), f"the README cites results/{cited}, which is not committed"
    assert inside == render(json.loads(results.read_text(encoding="utf-8")), cited)


def test_the_ranking_runs_from_the_largest_share_down() -> None:
    inside, _ = block((PERFORMANCE / "README.md").read_text(encoding="utf-8"))
    for table in inside.split("### Ranked:")[1:]:
        shares = [
            float(line.split("|")[3].strip().rstrip("%")) for line in table.splitlines() if line.startswith("| ") and "%" in line
        ]
        assert shares == sorted(shares, reverse=True)
