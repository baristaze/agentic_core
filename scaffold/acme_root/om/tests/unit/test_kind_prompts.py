"""A kind's prompt is a Markdown file beside its module, one per version,
read at import and pinned by its digest once released: a changed text
under a released version, a missing file, a file no version reads, and a
prompt past its bound are each refused, naming the file."""

import json
import re
from pathlib import Path

import pytest

from acme.om.agents.prompts import (
    LOCK,
    MAX_PROMPT,
    PROMPTS,
    digest,
    prompt_breaches,
    prompt_file,
    prompt_of,
)
from acme.om.agents.types.kind import AgentKind, DoneRule
from acme.om.attribution.types.authority import AuthorityMode
from acme.om.exceptions import UnsafeConfiguration

FIRST = "You read the ledger and answer with its totals.\n\nCite each line you use."
SECOND = "You read the ledger and answer with its totals, citing each line."


def folder_of(tmp_path: Path, *texts: str, pinned: int | None = None) -> Path:
    """A kind folder `ledger/` whose `prompts/v<N>.md` holds the Nth text,
    with a final line break as an editor leaves it, and a lock that pins
    the first `pinned` of them, every one by default."""
    folder = tmp_path / "kinds" / "ledger"
    (folder / PROMPTS).mkdir(parents=True)
    for version, text in enumerate(texts, start=1):
        (folder / PROMPTS / f"v{version}.md").write_text(text + "\n", encoding="utf-8")
    pins = {f"v{n}.md": digest(text) for n, text in enumerate(texts[:pinned], start=1)}
    (folder / PROMPTS / LOCK).write_text(json.dumps(pins), encoding="utf-8")
    return folder


def version(folder: Path, number: int) -> AgentKind:
    """Version `number` of the folder's kind, its prompt read from its file
    as its module reads it."""
    return AgentKind(
        name=folder.name,
        version=number,
        done_rule=DoneRule.ANSWER,
        authority=AuthorityMode.DELEGATED,
        prompts=(prompt_of(str(folder / "kind.py"), number),),
    )


def test_a_version_reads_its_own_file_less_its_final_line_break(tmp_path: Path) -> None:
    folder = folder_of(tmp_path, FIRST, SECOND)

    versions = (version(folder, 1), version(folder, 2))

    assert prompt_file(str(folder / "kind.py"), 2) == folder / PROMPTS / "v2.md"
    assert tuple(kind.prompts for kind in versions) == ((FIRST,), (SECOND,))
    assert prompt_breaches(folder, versions) == ()


# Check 2: a changed text under a released version, a missing file, an
# orphan file, and a prompt past its bound each fail, naming the file.


def test_a_prompt_changed_under_its_released_version_is_refused(tmp_path: Path) -> None:
    folder = folder_of(tmp_path, FIRST)
    (folder / PROMPTS / "v1.md").write_text(SECOND + "\n", encoding="utf-8")

    (breach,) = prompt_breaches(folder, (version(folder, 1),))

    assert breach.startswith("ledger/prompts/v1.md: changed under released version 1")


def test_a_version_the_lock_pins_nothing_for_is_refused_with_the_digest_to_pin(
    tmp_path: Path,
) -> None:
    folder = folder_of(tmp_path, FIRST, SECOND, pinned=1)

    (breach,) = prompt_breaches(folder, (version(folder, 1), version(folder, 2)))

    assert breach.startswith(f"ledger/prompts/v2.md: {LOCK} pins no digest for it")
    assert digest(SECOND) in breach


def test_a_referenced_prompt_file_missing_is_refused(tmp_path: Path) -> None:
    folder = folder_of(tmp_path, FIRST)
    released = version(folder, 1)
    (folder / PROMPTS / "v1.md").unlink()

    with pytest.raises(UnsafeConfiguration, match=re.escape("ledger/prompts/v1.md: no such")):
        version(folder, 1)
    assert prompt_breaches(folder, (released,)) == ("ledger/prompts/v1.md: no such prompt file",)


def test_a_prompt_file_no_version_reads_is_refused(tmp_path: Path) -> None:
    folder = folder_of(tmp_path, FIRST, SECOND)

    breaches = prompt_breaches(folder, (version(folder, 1),))

    assert breaches == ("ledger/prompts/v2.md: no version of ledger reads it",)


def test_a_prompt_past_its_bound_is_refused(tmp_path: Path) -> None:
    folder = folder_of(tmp_path, FIRST)
    released = version(folder, 1)
    (folder / PROMPTS / "v1.md").write_text("x" * (MAX_PROMPT + 1), encoding="utf-8")

    past = f"ledger/prompts/v1.md: {MAX_PROMPT + 1} characters, past the bound of {MAX_PROMPT}"
    with pytest.raises(UnsafeConfiguration, match=re.escape(past)):
        version(folder, 1)
    assert prompt_breaches(folder, (released,)) == (past,)


def test_a_version_whose_prompts_are_not_its_files_text_is_refused(tmp_path: Path) -> None:
    folder = folder_of(tmp_path, FIRST)
    written = version(folder, 1).model_copy(update={"prompts": (FIRST, "Answer briefly.")})

    (breach,) = prompt_breaches(folder, (written,))

    assert breach == "ledger/prompts/v1.md: version 1 renders prompts other than its text"


def test_a_folder_holds_its_own_kind_alone(tmp_path: Path) -> None:
    folder = folder_of(tmp_path, FIRST)
    other = version(folder, 1).model_copy(update={"name": "auditor"})

    assert prompt_breaches(folder, (other,)) == (
        "ledger/: holds agent kind auditor, whose folder is its own",
        "ledger/prompts/v1.md: no version of ledger reads it",
    )


def test_a_lock_that_is_no_object_of_digests_is_refused(tmp_path: Path) -> None:
    folder = folder_of(tmp_path, FIRST)
    (folder / PROMPTS / LOCK).write_text(json.dumps([digest(FIRST)]), encoding="utf-8")

    breaches = prompt_breaches(folder, (version(folder, 1),))

    assert breaches[0] == f"ledger/prompts/{LOCK}: not an object of file names to digests"
