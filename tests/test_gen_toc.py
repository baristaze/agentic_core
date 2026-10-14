"""scripts/gen_toc.py: the spec's Contents follows its headings."""

import pytest

LISTED = "- [Steps](#steps)\n- [Loops and Sessions](#loops-and-sessions)\n"


@pytest.fixture
def toc(repo):
    return repo.script("gen_toc")


def test_current_contents_passes_check(repo, toc, capsys):
    assert toc.main(["--check"]) == 0
    assert "toc ok" in capsys.readouterr().out


def test_stale_contents_fails_check_and_leaves_the_file(repo, toc, capsys):
    before = repo.read("agentic_core_spec.md").replace("- [Loops and Sessions](#loops-and-sessions)\n", "")
    repo.write("agentic_core_spec.md", before)
    assert toc.main(["--check"]) == 1
    assert "agentic_core_spec.md: the Contents is stale (run `make gen-toc`)" in capsys.readouterr().out
    assert repo.read("agentic_core_spec.md") == before


def test_a_section_out_of_order_fails_check(repo, toc):
    repo.edit("agentic_core_spec.md", LISTED, "- [Loops and Sessions](#loops-and-sessions)\n- [Steps](#steps)\n")
    assert toc.main(["--check"]) == 1


def test_a_new_section_is_listed_when_regenerated(repo, toc):
    repo.write("agentic_core_spec.md", repo.read("agentic_core_spec.md") + "\n## History\n\nThe source of truth.\n")
    assert toc.main(["--check"]) == 1
    assert toc.main([]) == 0
    assert f"{LISTED}- [History](#history)\n\n## Steps" in repo.read("agentic_core_spec.md")
    assert toc.main(["--check"]) == 0


def test_only_sections_after_the_contents_are_listed(repo, toc):
    rendered = toc.render(repo.read("agentic_core_spec.md"))
    assert rendered == LISTED.rstrip("\n")
    assert "How to Read This" not in rendered
    assert "The Record" not in rendered
    assert "not a heading" not in rendered


def test_repeated_heading_gets_a_numbered_anchor_the_link_checker_accepts(repo, toc):
    links = repo.script("check_links")
    repo.write("agentic_core_spec.md", repo.read("agentic_core_spec.md") + "\n## Steps\n\nAgain.\n")
    toc.main([])
    assert "- [Steps](#steps-1)" in repo.read("agentic_core_spec.md")
    assert links.main() == 0


def test_missing_contents_fails(repo, toc, capsys):
    repo.edit("agentic_core_spec.md", "## Contents\n", "## Index\n")
    assert toc.main(["--check"]) == 1
    assert "agentic_core_spec.md: no ## Contents heading" in capsys.readouterr().out


@pytest.mark.parametrize("flag", ["--chekc", "--chec", "--check-only", "check"])
def test_unknown_argument_is_refused_and_writes_nothing(repo, toc, flag):
    before = repo.read("agentic_core_spec.md").replace("- [Steps](#steps)\n", "")
    repo.write("agentic_core_spec.md", before)
    with pytest.raises(SystemExit) as exit_:
        toc.main([flag])
    assert exit_.value.code == 2
    assert repo.read("agentic_core_spec.md") == before


def test_headings_inside_a_comment_are_left_out_and_anchor_nothing(repo, toc, capsys):
    links = repo.script("check_links")
    repo.edit(
        "agentic_core_spec.md",
        "never copies them.\n",
        "never copies them.\n\n<!-- agents-only\n## Hidden\n\nSee [the hidden part](#hidden).\n-->\n",
    )
    assert "Hidden" not in toc.render(repo.read("agentic_core_spec.md"))
    assert toc.main(["--check"]) == 0
    assert links.main() == 1
    assert "missing anchor #hidden" in capsys.readouterr().out
