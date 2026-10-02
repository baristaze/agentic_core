"""scripts/check_links.py: relative links and anchors resolve."""

import pytest


@pytest.fixture
def links(repo):
    return repo.script("check_links")


def test_valid_tree_passes(repo, links, capsys):
    assert links.main() == 0
    assert "links ok" in capsys.readouterr().out


def test_missing_file_fails(repo, links, capsys):
    repo.write("docs/extra.md", "# Extra\n\nSee [the spec](../missing.md).\n")
    assert links.main() == 1
    assert "docs/extra.md:3: missing file ../missing.md" in capsys.readouterr().out


def test_a_sibling_spec_named_by_a_relative_path_fails(repo, links, capsys):
    repo.edit("agentic_core_spec.md", "A step is written once.", "A fleet is [the platform's](platform_spec.md#money).")
    assert links.main() == 1
    assert "agentic_core_spec.md:" in capsys.readouterr().out


def test_a_sibling_spec_named_by_its_url_passes(repo, links):
    repo.edit(
        "agentic_core_spec.md",
        "A step is written once.",
        "A fleet is [the platform's][p].\n\n[p]: https://example.com/p.md#money",
    )
    assert links.main() == 0


def test_missing_anchor_in_own_file_fails(repo, links, capsys):
    repo.edit("agentic_core_spec.md", "(#loops-and-sessions)", "(#loops-and-session)")
    assert links.main() == 1
    assert "missing anchor #loops-and-session" in capsys.readouterr().out


def test_missing_anchor_in_other_file_fails(repo, links, capsys):
    repo.edit("README.md", "agentic_core_spec.md#loops-and-sessions", "agentic_core_spec.md#loops")
    assert links.main() == 1
    assert "README.md:3: missing anchor #loops in agentic_core_spec.md" in capsys.readouterr().out


def test_repeated_heading_resolves_with_the_generator_numbering(repo, links):
    # #the-record-1 is in the fixture already; a third copy would be -2
    repo.edit("agentic_core_spec.md", "[their record](#the-record-1)", "[their record](#the-record-2)")
    assert links.main() == 1


def test_heading_inside_fenced_code_is_not_an_anchor(repo, links, capsys):
    repo.edit("agentic_core_spec.md", "never copies them.", "never copies them. See [code](#not-a-heading-fenced-code).")
    assert links.main() == 1
    assert "missing anchor #not-a-heading-fenced-code" in capsys.readouterr().out


def test_link_text_wrapped_across_lines_is_still_checked(repo, links, capsys):
    repo.write("docs/extra.md", "# Extra\n\nSee [a link whose text\nwraps](../nowhere.md).\n")
    assert links.main() == 1
    assert "missing file ../nowhere.md" in capsys.readouterr().out


def test_leading_slash_resolves_against_the_repository_root(repo, links, capsys):
    repo.write("docs/sub/extra.md", "# Extra\n\nSee [the spec](/agentic_core_spec.md#steps).\n")
    assert links.main() == 0
    repo.write("docs/sub/extra.md", "# Extra\n\nSee [the spec](/docs/agentic_core_spec.md).\n")
    assert links.main() == 1
    assert "docs/sub/extra.md:3: missing file /docs/agentic_core_spec.md" in capsys.readouterr().out


@pytest.mark.parametrize("target", ["/../etc/hosts", "../../etc/hosts", "../../../../../../../../../etc/hosts"])
def test_link_that_leaves_the_repository_fails(repo, links, capsys, target):
    repo.write("docs/extra.md", f"# Extra\n\nSee [a file]({target}).\n")
    assert links.main() == 1
    assert f"docs/extra.md:3: {target} leaves the repository" in capsys.readouterr().out


def test_caches_are_not_scanned(repo, links):
    repo.write(".pytest_cache/README.md", "# Cache\n\n[x](missing.md)\n")
    assert links.main() == 0


def test_the_scaffolds_skills_are_scanned(repo, links, capsys):
    rel = "scaffold/acme_root/.agents/skills/ops-watch/SKILL.md"
    repo.write(rel, "# ops-watch\n\nSee [the runbook](../../../docs/runbooks/operate.md).\n")
    assert links.main() == 1
    assert f"{rel}:3: missing file ../../../docs/runbooks/operate.md" in capsys.readouterr().out
    repo.write("scaffold/acme_root/docs/runbooks/operate.md", "# Operate\n")
    assert links.main() == 0


def test_external_links_are_not_fetched(repo, links):
    repo.write("docs/extra.md", "# Extra\n\n[x](https://example.invalid/none) [m](mailto:a@b.c)\n")
    assert links.main() == 0


@pytest.mark.parametrize(
    "link",
    [
        "[the spec](../missing.md 'single-quoted title')",
        "[the spec](<../missing.md>)",
        "[see [the note]](../missing.md)",
        "[the spec][ref]\n\n[ref]: ../missing.md",
        "[the spec](../missing.md (a parenthesised title))",
    ],
)
def test_every_link_form_is_checked(repo, links, capsys, link):
    repo.write("docs/extra.md", f"# Extra\n\n{link}\n")
    assert links.main() == 1
    assert "missing file ../missing.md" in capsys.readouterr().out


def test_a_definition_inside_fenced_code_is_not_a_link(repo, links):
    repo.write("docs/extra.md", "# Extra\n\n```text\n[ref]: ../missing.md\n```\n")
    assert links.main() == 0


@pytest.mark.parametrize(
    "line",
    ["See section 4 for the details.", "## 2.1 The steps", "As subsection 3.2 says.", "See §4.", "See § 4.1."],
)
def test_a_section_by_number_fails(repo, links, capsys, line):
    repo.write("skills/extra.md", f"# Extra\n\n{line}\n")
    assert links.main() == 1
    assert "skills/extra.md:3:" in capsys.readouterr().out


def test_a_release_heading_in_the_changelog_is_a_version_not_a_section(repo, links):
    repo.write("CHANGELOG.md", "# Changelog\n\n## 0.1.0 (2026-10-01)\n")
    assert links.main() == 0


def test_a_link_inside_fenced_code_is_not_checked(repo, links, capsys):
    repo.write(
        "docs/extra.md",
        "# Extra\n\n```markdown\nSee [the spec](../missing.md).\n```\n\n~~~\n[x](#nowhere)\n~~~\n",
    )
    assert links.main() == 0, capsys.readouterr().out
