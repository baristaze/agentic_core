"""scripts/check_skills.py: skill frontmatter and the paths a skill names."""

import shutil
from pathlib import Path

import pytest

REVIEW = "skills/agentic-review-steps/SKILL.md"
SCAFFOLD = "skills/agentic-scaffold-tool/SKILL.md"
LINK = "scaffold/acme_root/.claude/skills"
"""Claude Code's folder of the scaffold's skills: a link to the folder every other agent reads."""
COPIED = "scaffold/acme_root/.agents/skills"
"""Where the scaffold keeps the skills a render carries and runs."""


@pytest.fixture
def skills(repo):
    return repo.script("check_skills")


def copied(name: str) -> str:
    """The path of one skill of the scaffold."""
    return f"{COPIED}/{name}/SKILL.md"


def scaffold_link(repo) -> Path:
    """The scaffold's `.claude/skills`, a link to `../.agents/skills`, as a render carries it."""
    link = repo.root / LINK
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to("../.agents/skills")
    return link


def set_description(repo, value: str) -> None:
    text = repo.read(REVIEW)
    head, _, tail = text.partition("\ndescription: ")
    _, _, tail = tail.partition("\n")
    repo.write(REVIEW, f"{head}\ndescription: {value}\n{tail}")


def test_valid_tree_passes(repo, skills, capsys):
    assert skills.main() == 0
    assert "skills ok: 2 skills, 0 scaffold skills" in capsys.readouterr().out


def test_no_skills_folder_passes(repo, skills, capsys):
    shutil.rmtree(repo.root / "skills")
    assert skills.main() == 0
    assert "skills ok: 0 skills, 0 scaffold skills" in capsys.readouterr().out


def test_name_must_equal_folder_and_start_with_agentic(repo, skills, capsys):
    repo.edit(REVIEW, "name: agentic-review-steps", "name: arch-review-steps")
    assert skills.main() == 1
    out = capsys.readouterr().out
    assert "name 'arch-review-steps' differs from folder 'agentic-review-steps'" in out
    assert "name 'arch-review-steps' must match" in out


def test_a_name_with_a_doubled_hyphen_fails(repo, skills, capsys):
    repo.write("skills/agentic--odd/SKILL.md", '---\nname: agentic--odd\ndescription: "Odd."\nallowed-tools: Read\n---\n')
    assert skills.main() == 1
    assert "skills/agentic--odd/SKILL.md: name 'agentic--odd' must match" in capsys.readouterr().out


def test_a_folder_without_a_skill_fails(repo, skills, capsys):
    repo.write("skills/agentic-explain/references/notes.md", "# Notes\n")
    assert skills.main() == 1
    assert "skills/agentic-explain: no SKILL.md" in capsys.readouterr().out


@pytest.mark.parametrize(
    "value, problem",
    [
        ('"Review', "unterminated quoted value"),
        ('"Review "the" steps"', "text after the closing quote"),
        ('"C:\\path\\to"', "bad escape \\p"),
        ('"code \\x4"', "bad escape \\x4"),
    ],
)
def test_invalid_double_quoted_value_fails(repo, skills, capsys, value, problem):
    set_description(repo, value)
    assert skills.main() == 1
    assert problem in capsys.readouterr().out


@pytest.mark.parametrize("value", ['"Review \\"steps\\": one group."', '"Tab\\tand \\u00e9."', '"Review."  # a comment'])
def test_valid_double_quoted_value_passes(repo, skills, value):
    set_description(repo, value)
    assert skills.main() == 0


@pytest.mark.parametrize("value", ["Review: the steps", "Review # steps", "'single quoted'", "[a, b]", "> folded"])
def test_plain_value_a_strict_loader_would_misread_fails(repo, skills, capsys, value):
    set_description(repo, value)
    assert skills.main() == 1
    assert "must be double-quoted for strict YAML" in capsys.readouterr().out


def test_an_unquoted_description_fails(repo, skills, capsys):
    set_description(repo, "Review the steps")
    assert skills.main() == 1
    assert f"{REVIEW}: description must be one double-quoted string" in capsys.readouterr().out


def test_indented_continuation_repeated_key_and_missing_space_fail(repo, skills, capsys):
    repo.edit(
        REVIEW, "allowed-tools: Read, Grep, Bash(git diff:*)\n", "allowed-tools: Read\n  Grep\nallowed-tools: Read\nlicense:MIT\n"
    )
    assert skills.main() == 1
    out = capsys.readouterr().out
    assert "frontmatter line is indented" in out
    assert "key 'allowed-tools' repeated" in out
    assert "frontmatter line is not `key: value`: 'license:MIT'" in out


def test_description_limits(repo, skills, capsys, monkeypatch):
    set_description(repo, '""')
    assert skills.main() == 1
    assert "empty description" in capsys.readouterr().out
    set_description(repo, '"' + "x" * 500 + '"')
    assert skills.main() == 0
    set_description(repo, '"' + "x" * 501 + '"')
    assert skills.main() == 1
    assert "limit 500" in capsys.readouterr().out
    set_description(repo, '"Review."')
    monkeypatch.setattr(skills, "DESCRIPTIONS_TOTAL", 20)
    assert skills.main() == 1
    assert "the descriptions total" in capsys.readouterr().out


def test_the_description_limits_leave_room_in_the_hosts_listing(skills):
    assert (skills.DESCRIPTION_LIMIT, skills.DESCRIPTIONS_TOTAL) == (500, 6000)


@pytest.mark.parametrize("line", ["allowed_tools: Read", "argument-hint: <scope>", "model: opus", "context: fork"])
def test_a_key_outside_the_standard_fails_in_a_skill_and_in_a_scaffold_skill(repo, skills, capsys, line):
    key = line.partition(":")[0]
    scaffold_link(repo)
    repo.edit(REVIEW, "---\n\n#", f"{line}\n---\n\n#")
    repo.write(copied("ops-watch"), f'---\nname: ops-watch\ndescription: "Watch."\nallowed-tools: Read\n{line}\n---\n')
    assert skills.main() == 1
    out = capsys.readouterr().out
    for rel in (REVIEW, copied("ops-watch")):
        assert f"{rel}: frontmatter key {key!r} is neither a field of the Agent Skills standard" in out


def test_the_standards_optional_fields_pass_and_compatibility_has_a_limit(repo, skills, capsys):
    repo.edit(REVIEW, "---\n\n#", 'license: Apache-2.0\ncompatibility: "Needs git."\n---\n\n#')
    assert skills.main() == 0
    repo.edit(REVIEW, 'compatibility: "Needs git."', f"compatibility: {'x' * 501}")
    assert skills.main() == 1
    assert "compatibility is 501 characters; the standard allows 1 to 500" in capsys.readouterr().out


def test_a_skill_a_person_starts_by_name_says_so_to_codex_too(repo, skills, capsys):
    repo.edit(REVIEW, "---\n\n#", "disable-model-invocation: true\n---\n\n#")
    assert skills.main() == 1
    assert (
        f"{REVIEW}: disable-model-invocation is true, but skills/agentic-review-steps/agents/openai.yaml does not set "
        "policy.allow_implicit_invocation: false" in capsys.readouterr().out
    )
    repo.write("skills/agentic-review-steps/agents/openai.yaml", "# Codex\npolicy:\n  allow_implicit_invocation: false\n")
    assert skills.main() == 0
    repo.edit(REVIEW, "disable-model-invocation: true\n", "")
    assert skills.main() == 1
    assert "turns implicit invocation off; say disable-model-invocation: true too" in capsys.readouterr().out


@pytest.mark.parametrize("line", ["", "allowed-tools: \n", 'allowed-tools: ""\n'])
def test_a_skill_without_allowed_tools_fails(repo, skills, capsys, line):
    repo.edit(REVIEW, "allowed-tools: Read, Grep, Bash(git diff:*)\n", line)
    assert skills.main() == 1
    assert f"{REVIEW}: no allowed-tools; a skill names the tools it runs" in capsys.readouterr().out


def test_allowed_tools_form(repo, skills, capsys):
    repo.edit(REVIEW, "allowed-tools: Read, Grep,", "allowed-tools: Read Grep,")
    assert skills.main() == 1
    assert "must be comma-separated" in capsys.readouterr().out
    repo.edit(REVIEW, "allowed-tools: Read Grep, Bash(git diff:*)", "allowed-tools: Read, Bash(git diff *)")
    assert skills.main() == 1
    assert "Bash(cmd:*) prefix form" in capsys.readouterr().out
    repo.edit(REVIEW, "Bash(git diff *)", "Bash")
    assert skills.main() == 1
    assert "a bare Bash is refused" in capsys.readouterr().out
    repo.edit(REVIEW, "allowed-tools: Read, Bash", "allowed-tools: Read, Bash(git diff:* )")
    assert skills.main() == 1
    assert "trailing space inside the parentheses of 'Bash(git diff:* )'" in capsys.readouterr().out


def test_an_mcp_tool_name_is_a_name(repo, skills, capsys):
    repo.edit(REVIEW, "allowed-tools: Read,", "allowed-tools: mcp__claude-in-chrome__navigate, Read,")
    assert skills.main() == 0
    repo.edit(REVIEW, "mcp__claude-in-chrome__navigate", "mcp__Claude Chrome__navigate")
    assert skills.main() == 1
    assert "is not Name or Name(rule)" in capsys.readouterr().out


@pytest.mark.parametrize(
    "rule",
    ["Bash(*)", "Bash(rm -rf /)", "Bash(curl*)", "Bash(git*:*)", "Bash(make check && rm -rf /)", "Bash(git diff | sh:*)"],
)
def test_a_bash_rule_in_neither_allowed_form_fails(repo, skills, capsys, rule):
    repo.edit(SCAFFOLD, "Bash(make check)", f"Bash(make check), {rule}")
    assert skills.main() == 1
    assert f"{rule!r} is neither the Bash(cmd:*) prefix form nor an exact Bash(make <target>)" in capsys.readouterr().out


@pytest.mark.parametrize("rule", ["Bash(make:*)", "Bash(make -C sub:*)", "Bash(make -k check)"])
def test_a_make_entry_without_a_target_fails(repo, skills, capsys, rule):
    repo.edit(SCAFFOLD, "Bash(make check)", f"Bash(make check), {rule}")
    assert skills.main() == 1
    assert f"{rule!r} names no make target" in capsys.readouterr().out


def test_a_make_target_the_body_never_runs_fails(repo, skills, capsys):
    repo.edit(SCAFFOLD, "Bash(make check)", "Bash(make check), Bash(make test:*), Bash(make che)")
    repo.edit(SCAFFOLD, "2. Run `make check`.", "2. Run `make check` and `make test-e2e`.\n\n```text\nmake che\n```\n")
    assert skills.main() == 1
    out = capsys.readouterr().out
    assert f"{SCAFFOLD}: allowed-tools names Bash(make test) but the body never runs make test" in out
    assert "never runs make che" in out
    assert "never runs make check" not in out
    repo.edit(SCAFFOLD, "`make test-e2e`", "`make test -q` and `make che`")
    assert skills.main() == 0


def test_a_path_from_the_skills_folder_must_exist_inside_the_repository(repo, skills, capsys):
    repo.edit(REVIEW, "`../../lenses/steps.md`", "`../../lenses/steps.md` and `../notes.md`")
    assert skills.main() == 1
    assert f"{REVIEW}: reference ../notes.md does not exist" in capsys.readouterr().out
    (repo.root.parent / "outside.md").write_text("# Outside\n", encoding="utf-8")
    repo.edit(REVIEW, "`../notes.md`", "`../../../outside.md`")
    assert skills.main() == 1
    assert "reference ../../../outside.md resolves outside the repository" in capsys.readouterr().out


def test_a_reference_files_paths_resolve_from_the_skills_folder(repo, skills, capsys):
    repo.write("skills/agentic-scaffold-tool/references/parts.md", "# Parts\n\nRead `references/gone.json`.\n")
    assert skills.main() == 1
    assert "skills/agentic-scaffold-tool/references/parts.md: reference references/gone.json does not exist" in (
        capsys.readouterr().out
    )
    repo.write("skills/agentic-scaffold-tool/references/gone.json", "{}\n")
    assert skills.main() == 0


def test_a_path_out_of_the_folder_says_to_resolve_the_folder_with_realpath(repo, skills, capsys):
    repo.edit(REVIEW, " as `realpath` resolves it", "")
    assert skills.main() == 1
    assert f"{REVIEW}: names a path out of its folder (../) and never says to read it as `realpath`" in capsys.readouterr().out


@pytest.mark.parametrize("variable", ["${CLAUDE_SKILL_DIR}", "${CLAUDE_PLUGIN_ROOT}", "${CLAUDE_PROJECT_DIR}", "$ARGUMENTS"])
def test_a_substitution_one_agent_makes_fails(repo, skills, capsys, variable):
    scaffold_link(repo)
    repo.edit(REVIEW, "`../../lenses/steps.md`", f"`{variable}/../../lenses/steps.md`")
    repo.write("agents/agentic-reviewer.md", f"# Reviewer\n\nRead `{variable}/agentic_core_spec.md`.\n")
    repo.write(
        copied("ops-watch"), f'---\nname: ops-watch\ndescription: "Watch."\nallowed-tools: Read\n---\n\nRead `{variable}`.\n'
    )
    assert skills.main() == 1
    out = capsys.readouterr().out
    for rel in (REVIEW, "agents/agentic-reviewer.md", copied("ops-watch")):
        assert f"{rel}: names {variable}, a substitution one agent makes and the others read as text" in out


def test_a_scaffold_skill_is_held_to_the_skill_frontmatter(repo, skills, capsys):
    scaffold_link(repo)
    good = '---\nname: ops-infra-as-code\ndescription: "Plan."\nallowed-tools: Read, Bash(aws:*)\n---\n\n# ops-infra-as-code\n'
    repo.write(copied("ops-infra-as-code"), good)
    assert skills.main() == 0
    assert "1 scaffold skills" in capsys.readouterr().out
    bad = "---\nname: ops-infra-as-cod\ndescription: Plan.\nallowed-tools: Read Bash\n---\n"
    repo.write(copied("ops-infra-as-code"), bad)
    assert skills.main() == 1
    out = capsys.readouterr().out
    assert "differs from its folder 'ops-infra-as-code'" in out
    assert "description must be one double-quoted string" in out
    assert "allowed-tools must be comma-separated" in out


def test_a_scaffold_skill_name_the_standard_refuses_and_a_missing_skill_fail(repo, skills, capsys):
    scaffold_link(repo)
    repo.write(copied("ops--watch"), '---\nname: ops--watch\ndescription: "Watch."\nallowed-tools: Read\n---\n')
    repo.write(f"{COPIED}/ops-report/references/notes.md", "# Notes\n")
    assert skills.main() == 1
    out = capsys.readouterr().out
    assert f"{copied('ops--watch')}: name 'ops--watch' is not lowercase words joined by one hyphen each" in out
    assert f"{COPIED}/ops-report: no SKILL.md" in out


def test_a_scaffold_skill_without_allowed_tools_fails(repo, skills, capsys):
    scaffold_link(repo)
    repo.write(copied("ops-watch"), '---\nname: ops-watch\ndescription: "Watch an environment."\n---\n')
    assert skills.main() == 1
    assert f"{copied('ops-watch')}: no allowed-tools" in capsys.readouterr().out


def test_a_scaffold_skill_path_resolves_inside_the_tree_a_render_carries(repo, skills, capsys):
    scaffold_link(repo)
    head = '---\nname: ops-infra-as-code\ndescription: "Plan."\nallowed-tools: Read\n---\n\n'
    repo.write(f"{COPIED}/_shared/ops-preamble.md", "# Preamble\n")
    repo.write(copied("ops-infra-as-code"), head + "Read `../_shared/ops-preamble.md` first.\n")
    assert skills.main() == 0
    repo.write(copied("ops-infra-as-code"), head + "Read `../_shared/gone.md` first.\n")
    assert skills.main() == 1
    assert f"{copied('ops-infra-as-code')}: reference ../_shared/gone.md does not exist" in capsys.readouterr().out
    repo.write("scaffold/outside.md", "# Outside\n")
    repo.write(copied("ops-infra-as-code"), head + "Read `../../../../outside.md` first.\n")
    assert skills.main() == 1
    assert "reference ../../../../outside.md resolves outside scaffold/acme_root" in capsys.readouterr().out


def test_the_scaffolds_claude_skills_is_a_link_to_its_agents_skills(repo, skills, capsys):
    repo.write(copied("ops-watch"), '---\nname: ops-watch\ndescription: "Watch."\nallowed-tools: Read\n---\n')
    assert skills.main() == 1
    assert f"{LINK}: not a link" in capsys.readouterr().out
    link = scaffold_link(repo)
    assert skills.main() == 0
    link.unlink()
    link.symlink_to("../skills")
    assert skills.main() == 1
    assert f"{LINK}: links to '../skills'; it links to '../.agents/skills'" in capsys.readouterr().out
