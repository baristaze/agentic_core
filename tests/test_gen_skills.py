"""scripts/gen_skills.py: the review skills follow the template and the lens table."""

import pytest

from conftest import render_template

TEMPLATE = "skills/_template/review.SKILL.md"
STEPS = "skills/agentic-review-steps/SKILL.md"


@pytest.fixture
def gen(repo):
    return repo.script("gen_skills")


def test_generated_skills_pass_check(repo, gen, capsys):
    assert gen.main(["--check"]) == 0
    assert "generated skills ok: 1 review skills" in capsys.readouterr().out


def test_a_stale_skill_fails_check_and_is_left_alone(repo, gen, capsys):
    repo.edit(TEMPLATE, "Never edit", "Never ever edit")
    before = repo.read(STEPS)
    assert gen.main(["--check"]) == 1
    assert STEPS in capsys.readouterr().out
    assert repo.read(STEPS) == before


def test_a_lens_title_or_covers_change_makes_the_skill_stale(repo, gen, capsys):
    repo.edit("lenses/README.md", "| Steps  |\n", "| Steps; History |\n")
    assert gen.main(["--check"]) == 1
    assert STEPS in capsys.readouterr().out
    assert gen.main([]) == 0
    assert repo.read(STEPS) == render_template("steps", "Steps", "Steps; History")


def test_regenerate_writes_every_group_of_the_table(repo, gen, capsys):
    covers = "Identity, Trust, and Attribution"
    repo.edit("lenses/README.md", "| Steps  |\n", f"| Steps  |\n| `trust`  | `TRU`  | `trust.md` | {covers} |\n")
    repo.write("lenses/trust.md", "# Trust\n")
    assert gen.main([]) == 0
    assert "1 written, 1 unchanged" in capsys.readouterr().out
    assert repo.read("skills/agentic-review-trust/SKILL.md") == render_template("trust", "Trust", covers)


def test_a_lens_file_without_a_title_stops_generation(repo, gen):
    repo.write("lenses/steps.md", "Group id: `steps`.\n")
    with pytest.raises(SystemExit):
        gen.main(["--check"])


def test_an_unknown_argument_is_refused_and_writes_nothing(repo, gen):
    repo.edit(TEMPLATE, "Never edit", "Never ever edit")
    before = repo.read(STEPS)
    with pytest.raises(SystemExit) as exit_:
        gen.main(["--chekc"])
    assert exit_.value.code == 2
    assert repo.read(STEPS) == before
