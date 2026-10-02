"""scripts/check_version.py: every copy of the version agrees with plugin.json."""

import pytest


@pytest.fixture
def version(repo):
    return repo.script("check_version")


def test_agreeing_copies_pass(repo, version, capsys):
    assert version.main() == 0
    assert "version ok: 1.2.3" in capsys.readouterr().out


def test_marketplace_mismatch_fails(repo, version, capsys):
    repo.edit(".claude-plugin/marketplace.json", '"version": "1.2.3"', '"version": "1.2.4"')
    assert version.main() == 1
    assert "plugins[0].version is '1.2.4', plugin.json says '1.2.3'" in capsys.readouterr().out


def test_changelog_latest_release_must_match(repo, version, capsys):
    repo.edit("CHANGELOG.md", "## 1.2.3 (2026-01-01)", "## 1.3.0 (2026-02-01)")
    assert version.main() == 1
    assert "CHANGELOG.md:5: latest release is 1.3.0, plugin.json says 1.2.3" in capsys.readouterr().out


def test_a_released_version_without_a_changelog_release_fails(repo, version, capsys):
    repo.write("CHANGELOG.md", "# Changelog\n\n## Unreleased\n")
    assert version.main() == 1
    assert "CHANGELOG.md: no release heading, and plugin.json says 1.2.3" in capsys.readouterr().out


def test_before_the_first_release_the_version_is_zero_and_the_changelog_has_no_release(repo, version, capsys):
    repo.write("CHANGELOG.md", "# Changelog\n\nReleases are tagged `vMAJOR.MINOR.PATCH`.\n")
    repo.write(".claude-plugin/plugin.json", '{"name": "agentic-core", "version": "0.0.0"}\n')
    repo.write(".claude-plugin/marketplace.json", '{"plugins": [{"name": "agentic-core", "version": "0.0.0"}]}\n')
    assert version.main() == 0
    assert "version ok: 0.0.0" in capsys.readouterr().out
    repo.write("CHANGELOG.md", "# Changelog\n\n## 0.1.0 (2026-10-01)\n")
    assert version.main() == 1
    assert "latest release is 0.1.0, plugin.json says 0.0.0" in capsys.readouterr().out


def test_plugin_version_must_be_semver(repo, version, capsys):
    repo.write(".claude-plugin/plugin.json", '{"version": "v1.2"}\n')
    assert version.main() == 1
    assert "is not MAJOR.MINOR.PATCH" in capsys.readouterr().out


def test_the_scaffolds_checker_carries_the_version(repo, version, capsys):
    init = "scaffold/acme_root/checkers/src/acme/agentic_check/__init__.py"
    repo.write(init, '__version__ = "1.2.3"\n')
    assert version.main() == 0
    capsys.readouterr()
    repo.edit(init, '"1.2.3"', '"1.2.4"')
    assert version.main() == 1
    assert f"{init}: __version__ is '1.2.4', plugin.json says '1.2.3'" in capsys.readouterr().out
