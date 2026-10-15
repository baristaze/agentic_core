"""`ops-agent-session-spend` reconciles one agent session's model spend,
loop by loop, from the usage records, read through the operator plane
with a read-only token."""

import pytest
from skills_support import prose_of

NAME = "ops-agent-session-spend"

# Every loop it runs, stated with its count (`skills_support` says why).
COUNT_BOUNDS = [
    "Read at most 5 pages, 1,000 records.",
    "Run it over at most 10 loops",
    "never more than 5 pages, and never more than 10 loops read turn by turn",
]


@pytest.mark.parametrize("bound", COUNT_BOUNDS)
def test_every_loop_it_runs_states_its_count(bound: str) -> None:
    assert bound in prose_of(NAME), f"{NAME} no longer says: {bound}"
