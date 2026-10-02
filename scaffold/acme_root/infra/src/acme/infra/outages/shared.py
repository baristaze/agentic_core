"""The outage signal on the shared cache: one entry per provider and
credential, under the platform's own scope, living until the outage's retry
time. Over the memory cache it serves one process; over Valkey a fleet
shares it, so one session's discovery parks every other.

It fails open, as the cache does: a cache that cannot be reached is a miss,
and a miss says nothing is known."""

from datetime import datetime, timedelta
from urllib.parse import quote

from pydantic import ValidationError

from acme.infra.base import SYSTEM_SCOPE
from acme.infra.cache import CacheInterface
from acme.infra.outages import Outage, OutageSignalInterface

LEAST_TTL = timedelta(milliseconds=1)


def outage_key(provider: str, credential: str) -> str:
    """One key per pair. Each part is quoted, so no credential's name can
    reach another pair's key by holding the separator."""
    return f"outage:{quote(provider, safe='')}:{quote(credential, safe='')}"


class OutageSignalCacheImpl(OutageSignalInterface):
    def __init__(self, cache: CacheInterface) -> None:
        self._cache = cache

    async def report(self, outage: Outage, now: datetime) -> None:
        if outage.retry_at <= now:
            return
        # A read, then a write: two reports that race may leave the earlier
        # retry time of the two. That costs one early call to a provider still
        # failing, which reports again; it never parks anyone for longer.
        held = await self.current(outage.provider, outage.credential, now)
        if held is not None and held.retry_at >= outage.retry_at:
            return
        ttl = max(outage.retry_at - now, LEAST_TTL)
        key = outage_key(outage.provider, outage.credential)
        await self._cache.put(SYSTEM_SCOPE, key, outage.model_dump_json().encode(), ttl)

    async def current(self, provider: str, credential: str, now: datetime) -> Outage | None:
        value = await self._cache.get(SYSTEM_SCOPE, outage_key(provider, credential))
        if value is None:
            return None
        try:
            outage = Outage.model_validate_json(value)
        except ValidationError:
            return None  # written by nothing that reports: nothing is known
        if (outage.provider, outage.credential) != (provider, credential):
            return None
        return outage if now < outage.retry_at else None

    async def clear(self, provider: str, credential: str) -> None:
        await self._cache.invalidate(SYSTEM_SCOPE, outage_key(provider, credential))

    def describe(self) -> str:
        return f"outages=shared({self._cache.describe()})"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None
