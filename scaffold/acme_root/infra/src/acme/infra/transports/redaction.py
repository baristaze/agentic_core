"""Redaction of what a process prints, before anything streams or returns.

A secret in a process the agent controls is assumed disclosed: redaction
stops an accidental display, never a deliberate leak. So it matches the
forms a value takes when a program prints it by accident: the value raw,
encoded as base64 (the standard and the URL alphabets, padded or not, and
at each of the three offsets the value can take inside a longer encoded
run), as hex, and escaped for a URL, for JSON, and for a Python literal.
A form shorter than `MIN_ENCODED` is matched raw only: shorter runs of an
encoding turn up in ordinary text.

A stream is redacted as it arrives with a holdback as long as the longest
form less one character, so a value split across two chunks is still
caught: text leaves only once no form can still be completing in it."""

import base64
import binascii
import json
import re
from collections.abc import Iterator, Mapping
from urllib.parse import quote, quote_plus

MIN_ENCODED = 8
"""The shortest encoded or escaped form matched; the raw value always is."""


def marker(name: str) -> str:
    """What stands in for a value: the secret's name, never a part of it."""
    return f"[redacted:{name}]"


def _base64_runs(raw: bytes) -> set[str]:
    """The base64 of `raw` at each offset it can take in a longer run: the
    characters every encoding of it carries whatever bytes surround it."""
    runs: set[str] = set()
    for offset, skip in ((0, 0), (1, 2), (2, 3)):
        total = offset + len(raw)
        whole = (8 * total) // 6  # characters whose six bits are all in the data
        for encode in (base64.b64encode, base64.urlsafe_b64encode):
            encoded = encode(b"\0" * offset + raw).decode()
            runs.add(encoded[skip:whole])
            if offset == 0:
                runs.add(encoded)  # padded, as a value encoded alone prints
    return runs


def forms(value: str) -> set[str]:
    """Every form of `value` that redaction matches."""
    raw = value.encode()
    encoded = {
        *_base64_runs(raw),
        binascii.hexlify(raw).decode(),
        binascii.hexlify(raw).decode().upper(),
        quote(value, safe=""),
        quote_plus(value, safe=""),
        json.dumps(value)[1:-1],
        json.dumps(value, ensure_ascii=False)[1:-1],
        repr(value)[1:-1],
    }
    return {value} | {form for form in encoded if len(form) >= MIN_ENCODED}


class Redactor:
    """Redacts the secrets of one command: a mapping of each secret's name to
    its value. With none, it passes text as it is."""

    def __init__(self, secrets: Mapping[str, str]) -> None:
        names: dict[str, str] = {}
        for name, value in secrets.items():
            if value:
                for form in forms(value):
                    names.setdefault(form, name)
        self._names = names
        # Longest first, so a form that holds a shorter one is matched whole.
        ordered = sorted(names, key=len, reverse=True)
        self._pattern = re.compile("|".join(map(re.escape, ordered))) if ordered else None
        self.holdback = max((len(form) for form in names), default=1) - 1

    def matches(self, text: str) -> Iterator[tuple[int, int, str]]:
        """Where each form starts and ends in `text`, and whose it is."""
        if self._pattern is not None:
            for match in self._pattern.finditer(text):
                yield match.start(), match.end(), self._names[match.group(0)]

    def redact(self, text: str) -> str:
        return StreamRedactor(self).flush(text)

    def stream(self) -> StreamRedactor:
        return StreamRedactor(self)


class StreamRedactor:
    """One stream's redaction, chunk by chunk: `feed` answers what may leave
    now, and `flush` the rest once the stream ends. Text leaves only once no
    form can still be completing in it: a form that starts before the last
    `holdback` characters is whole already, the longest one at its place."""

    def __init__(self, redactor: Redactor) -> None:
        self._redactor = redactor
        self._held = ""

    def feed(self, chunk: str) -> str:
        return self._release(self._held + chunk, len(self._held + chunk) - self._redactor.holdback)

    def flush(self, chunk: str = "") -> str:
        return self._release(self._held + chunk, len(self._held + chunk))

    def _release(self, text: str, cut: int) -> str:
        pieces: list[str] = []
        position = 0
        for start, end, name in self._redactor.matches(text):
            if start >= cut:
                break
            pieces += [text[position:start], marker(name)]
            position = end
        release = max(cut, position)
        pieces.append(text[position:release])
        self._held = text[release:]
        return "".join(pieces)
