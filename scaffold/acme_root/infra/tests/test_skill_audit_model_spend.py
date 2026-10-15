"""`audit-model-spend` audits what an org's model calls spend, from the
usage records of the sessions named, read through the operator plane
with a read-only token."""

import pytest
from skills_support import prose_of

NAME = "audit-model-spend"

# Every loop it runs, stated with its count (`skills_support` says why).
COUNT_BOUNDS = [
    "The audit reads at most 20 sessions",
    "Read at most 5 pages a session, 1,000 records.",
    "never more than 20 sessions, and never more than 5 pages of one",
]


@pytest.mark.parametrize("bound", COUNT_BOUNDS)
def test_every_loop_it_runs_states_its_count(bound: str) -> None:
    assert bound in prose_of(NAME), f"{NAME} no longer says: {bound}"
