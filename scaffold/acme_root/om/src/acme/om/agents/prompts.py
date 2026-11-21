"""A kind's prompt is Markdown, never a string in code. A product keeps each
kind in a folder of its own, `kinds/<kind>/`, beside the others and never
inside one. The module at its root holds an `AgentKind` constant for each
version, and `prompts/v<N>.md` holds the prompt of version N, which that
constant reads at import (`prompt_of`). The prompt is the file's text,
less the one line break that ends it.

`prompts/lock.json` pins the prompt of each released version by its
digest. A version's entry is written once, when its text is final, and
never changes or goes: a prompt changed in place under a released
version fails the check (`prompt_breaches`) until the change is a
version of its own, so a session that pinned a version renders the text
it started with. The engine reads the module and `prompts/` alone; a
layer above may keep folders of its own beside them."""

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path

from acme.om.agents.types.kind import AgentKind
from acme.om.agents.types.request import MAX_STANDING
from acme.om.exceptions import UnsafeConfiguration

PROMPTS = "prompts"
"""The folder beside a kind's module that holds its versions' prompts."""

LOCK = "lock.json"
"""The file in `PROMPTS` that pins each released version's prompt by its
digest: `{"v<N>.md": "<sha-256 of its text>"}`."""

MAX_PROMPT = MAX_STANDING
"""The most a kind version's prompt holds, in characters. It renders whole
in the system layer of every request, as the standing text does, so it
takes the standing text's bound."""


def prompt_file(module: str, version: int) -> Path:
    """The file that holds the prompt of version `version` of the kind whose
    module is `module`, the path its `__file__` names."""
    return Path(module).parent / PROMPTS / f"v{version}.md"


def prompt_of(module: str, version: int) -> str:
    """The prompt of version `version` of the kind whose module is `module`,
    read when that module is imported. A file missing, not UTF-8, or past
    `MAX_PROMPT` refuses the import, naming the file."""
    text, refusal = _read(prompt_file(module, version))
    if refusal is not None:
        raise UnsafeConfiguration(refusal)
    return text


def digest(prompt: str) -> str:
    """The digest the lock pins a prompt by: the SHA-256 of its UTF-8 text."""
    return hashlib.sha256(prompt.encode()).hexdigest()


def prompt_breaches(folder: Path, versions: Iterable[AgentKind]) -> tuple[str, ...]:
    """What the kind folder `folder` breaks, each breach naming its file:
    a version of another kind than the folder's; a version whose prompt
    file is missing, not UTF-8, or past `MAX_PROMPT`; a version whose
    prompts are not its own file's text; a version the lock pins no digest
    for, or pins another one, which is a released prompt changed in place;
    and a file under `prompts/` that no version reads. `versions` are the
    constants of the folder's module."""
    prompts = folder / PROMPTS
    pins, breaches = _pins(prompts / LOCK)
    read: set[str] = set()
    for kind in versions:
        if kind.name != folder.name:
            breaches.append(
                f"{folder.name}/: holds agent kind {kind.name}, whose folder is its own"
            )
            continue
        path = prompts / f"v{kind.version}.md"
        named = _named(path)
        read.add(path.name)
        text, refusal = _read(path)
        if refusal is not None:
            breaches.append(refusal)
            continue
        if kind.prompts != (text,):
            breaches.append(f"{named}: version {kind.version} renders prompts other than its text")
        pinned = pins.get(path.name)
        if pinned is None:
            breaches.append(
                f"{named}: {LOCK} pins no digest for it; once its text is final, "
                f'pin "{path.name}": "{digest(text)}"'
            )
        elif pinned != digest(text):
            breaches.append(
                f"{named}: changed under released version {kind.version}, whose text stays as "
                f"{LOCK} pins it; a changed prompt is a version of its own"
            )
    if prompts.is_dir():
        for path in sorted(prompts.iterdir()):
            if path.name != LOCK and path.name not in read:
                breaches.append(f"{_named(path)}: no version of {folder.name} reads it")
    return tuple(breaches)


def _named(path: Path) -> str:
    """A file named from its kind's folder on: `<kind>/prompts/<file>`."""
    return "/".join(path.parts[-3:])


def _read(path: Path) -> tuple[str, str | None]:
    """A prompt file's text, and why it is refused, or None."""
    try:
        text = path.read_text(encoding="utf-8").removesuffix("\n")
    except FileNotFoundError:
        return "", f"{_named(path)}: no such prompt file"
    except UnicodeDecodeError:
        return "", f"{_named(path)}: not UTF-8"
    if len(text) > MAX_PROMPT:
        return text, f"{_named(path)}: {len(text)} characters, past the bound of {MAX_PROMPT}"
    return text, None


def _pins(lock: Path) -> tuple[dict[str, str], list[str]]:
    """The digests the lock pins, by file, and why it is refused: a lock
    that is not an object of file names to digests pins nothing."""
    if not lock.is_file():
        return {}, []
    try:
        pins = json.loads(lock.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as refused:
        return {}, [f"{_named(lock)}: not JSON ({refused})"]
    if not isinstance(pins, dict) or not all(
        isinstance(name, str) and isinstance(pinned, str) for name, pinned in pins.items()
    ):
        return {}, [f"{_named(lock)}: not an object of file names to digests"]
    return pins, []
