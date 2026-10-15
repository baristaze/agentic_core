"""`ops-loop-spend` reconciles one loop's model spend from the usage
records, read through the operator plane with a read-only token."""

import pytest
from skills_support import prose_of

NAME = "ops-loop-spend"

# Every loop it runs, stated with its count (`skills_support` says why).
COUNT_BOUNDS = [
    "Read at most 5 pages, 1,000 records.",
    "never more than 5 pages",
]


@pytest.mark.parametrize("bound", COUNT_BOUNDS)
def test_every_loop_it_runs_states_its_count(bound: str) -> None:
    assert bound in prose_of(NAME), f"{NAME} no longer says: {bound}"
