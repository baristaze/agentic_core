"""scripts/check_agents.py: every subagent's frontmatter names it, describes it, and caps its turns."""

import shutil

import pytest

AGENT = "agents/agentic-reviewer.md"


@pytest.fixture
def agents(repo):
    return repo.script("check_agents")


def test_valid_tree_passes(repo, agents, capsys):
    assert agents.main() == 0
    assert "agents ok: 1 agent(s)" in capsys.readouterr().out


def test_no_agents_folder_passes(repo, agents, capsys):
    shutil.rmtree(repo.root / "agents")
    assert agents.main() == 0
    assert "agents ok: 0 agent(s)" in capsys.readouterr().out


def test_name_must_equal_the_file_and_start_with_agentic(repo, agents, capsys):
    repo.edit(AGENT, "name: agentic-reviewer", "name: reviewer")
    assert agents.main() == 1
    out = capsys.readouterr().out
    assert f"{AGENT}: name 'reviewer' differs from its file 'agentic-reviewer'" in out
    assert f"{AGENT}: name 'reviewer' must match" in out


def test_an_unquoted_or_empty_description_fails(repo, agents, capsys):
    repo.edit(AGENT, 'description: "Reviews a scope through one lens group of the agentic_core spec."', "description: Reviews.")
    assert agents.main() == 1
    assert f"{AGENT}: description must be one double-quoted string" in capsys.readouterr().out
    repo.edit(AGENT, "description: Reviews.", 'description: ""')
    assert agents.main() == 1
    assert f"{AGENT}: empty description" in capsys.readouterr().out


def test_a_missing_turn_cap_fails(repo, agents, capsys):
    repo.edit(AGENT, "maxTurns: 80\n", "")
    assert agents.main() == 1
    assert f"{AGENT}: no maxTurns in the frontmatter" in capsys.readouterr().out


@pytest.mark.parametrize("value", ["0", "-5", "eighty", '"80"', "8.5", ""])
def test_a_turn_cap_that_is_no_whole_number_above_zero_fails(repo, agents, capsys, value):
    repo.edit(AGENT, "maxTurns: 80", f"maxTurns: {value}")
    assert agents.main() == 1
    assert "not a whole number above zero" in capsys.readouterr().out


def test_a_file_without_frontmatter_fails(repo, agents, capsys):
    repo.write(AGENT, "You are a reviewer.\n")
    assert agents.main() == 1
    assert f"{AGENT}: missing frontmatter" in capsys.readouterr().out
