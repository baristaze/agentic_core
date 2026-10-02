"""Redaction matches a secret raw, encoded, and escaped, whole or split
across the chunks of a stream, and leaves the secret's name in its place."""

import base64
import json
from urllib.parse import quote

import pytest

from acme.infra.transports.redaction import Redactor, forms, marker

SECRET = 'tok-3f9A/b+c="q"\\9z'
NAME = "api_token"


def printed(value: str) -> dict[str, str]:
    """The ways a program prints a value by accident."""
    raw = value.encode()
    return {
        "raw": value,
        "base64": base64.b64encode(raw).decode(),
        "base64 unpadded": base64.b64encode(raw).decode().rstrip("="),
        "base64 url": base64.urlsafe_b64encode(raw).decode(),
        "base64 inside a longer run": base64.b64encode(b"Authorization: Basic " + raw).decode(),
        "base64 at an odd offset": base64.b64encode(b"x" + raw + b"y").decode(),
        "hex": raw.hex(),
        "url escaped": quote(value, safe=""),
        "json escaped": json.dumps({"token": value}),
        "repr": repr(value),
    }


@pytest.mark.parametrize("how", list(printed(SECRET)))
def test_every_form_of_the_secret_is_redacted(how: str) -> None:
    text = f"before {printed(SECRET)[how]} after"
    out = Redactor({NAME: SECRET}).redact(text)
    assert marker(NAME) in out
    for form in forms(SECRET):
        assert form not in out, (how, form)
    assert out.startswith("before") and out.endswith("after")


def test_a_value_split_across_chunks_is_held_back_until_it_is_whole() -> None:
    redactor = Redactor({NAME: SECRET})
    text = f"one {SECRET} two {base64.b64encode(SECRET.encode()).decode()} three"
    for size in (1, 2, 3, 5, 7, 11):
        stream = redactor.stream()
        parts = [stream.feed(text[i : i + size]) for i in range(0, len(text), size)]
        parts.append(stream.flush())
        joined = "".join(parts)
        assert joined == redactor.redact(text), size
        for part in parts:
            assert SECRET not in part
        for form in forms(SECRET):
            assert form not in joined


def test_with_no_secret_text_passes_as_it_is() -> None:
    redactor = Redactor({})
    stream = redactor.stream()
    assert redactor.redact("plain") == "plain"
    assert stream.feed("pla") + stream.feed("in") + stream.flush() == "plain"


def test_a_short_value_is_matched_raw_and_never_by_a_short_encoding() -> None:
    """A run of an encoding shorter than the floor turns up in ordinary text,
    so a short value is matched raw only."""
    assert forms("abc") == {"abc"}
    assert Redactor({"pin": "4711"}).redact("pin 4711, page 4711") == (
        "pin [redacted:pin], page [redacted:pin]"
    )
