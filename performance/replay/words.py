"""Synthetic words, from a fixed seed, and the marker planted in each.

The words are syllables strung at random: no word of any language, and the
same words for the same seed. A text of a given size is a slice of one long
pool of them, so making one costs a copy. Each text opens with the run's
marker, which no sealed byte at rest may hold."""

from __future__ import annotations

import random
import secrets

ONSETS = ("b", "d", "f", "g", "k", "l", "m", "n", "p", "r", "s", "t", "v", "z", "br", "kr", "st", "tr")
VOWELS = ("a", "e", "i", "o", "u", "ai", "ou")
POOL_CHARS = 1 << 20


def marker() -> str:
    """A marker for one run: a word no generator writes but this one."""
    return f"qx{secrets.token_hex(6)}qx"


class Words:
    def __init__(self, seed: int, mark: str) -> None:
        rng = random.Random(seed)
        parts: list[str] = []
        size = 0
        while size < POOL_CHARS:
            word = "".join(rng.choice(ONSETS) + rng.choice(VOWELS) for _ in range(rng.randint(1, 4)))
            parts.append(word)
            size += len(word) + 1
        self._pool = " ".join(parts)
        self._rng = rng
        self.mark = mark

    def text(self, size: int) -> str:
        """`size` characters of words, opening with the marker; never shorter
        than the marker and a space."""
        body = max(0, size - len(self.mark) - 1)
        if body > len(self._pool):
            repeats = body // len(self._pool) + 1
            source = self._pool * repeats
        else:
            source = self._pool
        start = self._rng.randrange(0, max(1, len(source) - body))
        return f"{self.mark} {source[start : start + body]}"
