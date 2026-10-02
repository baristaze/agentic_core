"""The outage signal of a process that keeps none: it never signals. One
process needs no signal, since its own sessions learn of an outage from the
provider's errors; what is lost is the head start a shared one gives."""

from datetime import datetime

from acme.infra.base import QuietNull
from acme.infra.outages import Outage, OutageSignalInterface


class OutageSignalNullImpl(OutageSignalInterface, QuietNull):
    """Quiet: a report records nothing, and nothing is ever known."""

    async def report(self, outage: Outage, now: datetime) -> None:
        return None

    async def current(self, provider: str, credential: str, now: datetime) -> Outage | None:
        return None

    async def clear(self, provider: str, credential: str) -> None:
        return None

    def describe(self) -> str:
        return "outages=none"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None
