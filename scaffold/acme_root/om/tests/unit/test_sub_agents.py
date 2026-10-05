"""Sub-agents through the engine's own tools, over the memory storage: the
cases the suite over Postgres runs too (`contracts.sub_agents`)."""

from pathlib import Path

import pytest
from contracts import sub_agents
from contracts.loops import loop_over


async def test_a_root_holds_ten_sub_agents_and_the_eleventh_is_refused(tmp_path: Path) -> None:
    loop = loop_over(tmp_path, kinds=sub_agents.KINDS)
    await sub_agents.a_root_holds_ten_sub_agents_and_the_eleventh_is_refused(loop)


async def test_a_third_level_starts_and_a_fourth_is_refused(tmp_path: Path) -> None:
    loop = loop_over(tmp_path, kinds=sub_agents.KINDS)
    await sub_agents.a_third_level_starts_and_a_fourth_is_refused(loop)


async def test_children_together_spend_no_more_than_the_trees_budget(tmp_path: Path) -> None:
    loop = loop_over(tmp_path, kinds=sub_agents.KINDS)
    await sub_agents.children_together_spend_no_more_than_the_trees_budget(loop)


async def test_a_parent_parks_on_its_children_and_a_report_wakes_it(tmp_path: Path) -> None:
    loop = loop_over(tmp_path, kinds=sub_agents.KINDS)
    await sub_agents.a_parent_parks_on_its_children_and_a_report_wakes_it(loop)


async def test_a_root_waits_after_each_of_seven_reports_and_reads_them_all(tmp_path: Path) -> None:
    loop = loop_over(tmp_path, kinds=sub_agents.KINDS)
    await sub_agents.a_root_waits_after_each_of_seven_reports_and_reads_them_all(loop)


async def test_the_deadline_ends_a_wait_on_children(tmp_path: Path) -> None:
    loop = loop_over(tmp_path, kinds=sub_agents.KINDS)
    await sub_agents.the_deadline_ends_a_wait_on_children(loop)


async def test_a_looser_kind_runs_no_call_its_parents_kind_would_hold(tmp_path: Path) -> None:
    loop = loop_over(tmp_path, kinds=sub_agents.KINDS)
    await sub_agents.a_looser_kind_runs_no_call_its_parents_kind_would_hold(loop)


async def test_a_report_that_lands_before_the_park_still_wakes_the_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = loop_over(tmp_path, kinds=sub_agents.KINDS)
    await sub_agents.a_report_that_lands_before_the_park_still_wakes_the_parent(loop, monkeypatch)


async def test_a_spawn_asked_twice_starts_one_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = loop_over(tmp_path, kinds=sub_agents.KINDS)
    await sub_agents.a_spawn_asked_twice_starts_one_child(loop, monkeypatch)


async def test_a_session_with_a_sub_agent_at_work_below_it_is_not_deleted(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path, kinds=sub_agents.KINDS)
    await sub_agents.a_session_with_a_sub_agent_at_work_below_it_is_not_deleted(loop)


async def test_a_deleted_session_still_holds_the_sub_agents_below_it_to_its_kind(
    tmp_path: Path,
) -> None:
    loop = loop_over(tmp_path, kinds=sub_agents.KINDS)
    await sub_agents.a_deleted_session_still_holds_the_sub_agents_below_it_to_its_kind(loop)


async def test_a_sub_agent_whose_ancestor_is_purged_runs_no_loop(tmp_path: Path) -> None:
    loop = loop_over(tmp_path, kinds=sub_agents.KINDS)
    purging = loop.storage.get_agent_session_storage()
    await sub_agents.a_sub_agent_whose_ancestor_is_purged_runs_no_loop(loop, purging)
