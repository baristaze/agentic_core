"""The shared cache as a point leaves it, read on Valkey's own terms.

Before a point, Valkey is emptied, so what it holds after is the point's.
While the point runs, a thread samples the server's used memory, and keeps
its peak: the server's own peak counts from its start, and no command
resets it. Once the point is done, every key is read: its scope, from the
prefix the cache impl gives it (`acme:cache:<scope>:`), the bytes Valkey
gives it (`MEMORY USAGE`), and whether it carries a TTL. The engine puts
nothing with no TTL, so a key without one is counted, and named by scope."""

from __future__ import annotations

import asyncio
import threading
from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from glide import GlideClient, GlideClientConfiguration, NodeAddress

SAMPLE_SECONDS = 0.2
"""How often the thread reads the server's used memory."""
PREFIX = "acme:cache:"


async def connect(url: str) -> GlideClient:
    parts = urlsplit(url)
    database = parts.path.lstrip("/")
    return await GlideClient.create(
        GlideClientConfiguration(
            [NodeAddress(parts.hostname or "127.0.0.1", parts.port or 6379)],
            database_id=int(database) if database else 0,
        )
    )


def _text(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


async def used_memory(client: GlideClient) -> int:
    info = _text(await client.custom_command(["INFO", "memory"]))
    line = next(line for line in info.splitlines() if line.startswith("used_memory:"))
    return int(line.split(":", 1)[1])


def scope_of(key: str) -> str:
    if not key.startswith(PREFIX):
        return "outside the cache"
    return key[len(PREFIX) :].split(":", 1)[0]


@dataclass
class CacheSeen:
    empty_bytes: int = 0
    peak_bytes: int = 0
    keys: Counter[str] = field(default_factory=Counter)
    key_bytes: Counter[str] = field(default_factory=Counter)
    without_ttl: Counter[str] = field(default_factory=Counter)

    def as_dict(self) -> dict[str, Any]:
        return {
            "empty_bytes": self.empty_bytes,
            "peak_bytes": self.peak_bytes,
            "keys": dict(sorted(self.keys.items())),
            "key_bytes": dict(sorted(self.key_bytes.items())),
            "without_ttl": dict(sorted(self.without_ttl.items())),
        }


class CacheWatch:
    """Valkey emptied for one point, its memory sampled on a thread of its
    own while the point runs, and its keys read once it is done."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.seen = CacheSeen()

    def start(self) -> None:
        asyncio.run(self._empty())
        self._thread = threading.Thread(target=lambda: asyncio.run(self._sample()), name="performance-cache")
        self._thread.start()

    def stop(self) -> CacheSeen:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        asyncio.run(self._read_keys())
        return self.seen

    async def _empty(self) -> None:
        client = await connect(self._url)
        try:
            await client.custom_command(["FLUSHALL", "SYNC"])
            self.seen.empty_bytes = await used_memory(client)
        finally:
            await client.close()

    async def _sample(self) -> None:
        client = await connect(self._url)
        try:
            while not self._stop.is_set():
                self.seen.peak_bytes = max(self.seen.peak_bytes, await used_memory(client))
                await asyncio.sleep(SAMPLE_SECONDS)
            self.seen.peak_bytes = max(self.seen.peak_bytes, await used_memory(client))
        finally:
            await client.close()

    async def _read_keys(self) -> None:
        client = await connect(self._url)
        try:
            cursor = "0"
            while True:
                answer = await client.custom_command(["SCAN", cursor, "COUNT", "1000"])
                assert isinstance(answer, list)
                cursor, keys = _text(answer[0]), answer[1]
                for raw in keys:
                    key = _text(raw)
                    scope = scope_of(key)
                    self.seen.keys[scope] += 1
                    self.seen.key_bytes[scope] += int(await client.custom_command(["MEMORY", "USAGE", key]) or 0)
                    if int(await client.custom_command(["PTTL", key])) == -1:
                        self.seen.without_ttl[scope] += 1
                if cursor == "0":
                    return
        finally:
            await client.close()
