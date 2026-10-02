"""The outage signal: what the engine knows of a provider that is failing for
one credential, so a session parks at once instead of spending its retries
on a provider that fails fast. It is keyed by provider and credential, never
by provider alone: one tenant's revoked key is no outage of the platform's.

One process needs none, and its null object never signals. A fleet shares
one, on the shared cache, so the first session that learns of an outage
parks every other session that would call the same provider on the same
credential. A signal that cannot reach its store answers that nothing is
known: it fails open, and the provider's own errors still park."""

from abc import ABC, abstractmethod
from datetime import datetime

from pydantic import Field

from acme.infra.base import InfraModel

MAX_NAME = 200


class Outage(InfraModel):
    """A provider known to be failing for one credential until `retry_at`.
    `credential` names the key, never holds it: `platform` for the
    platform's own, or a tenant key's id. `kind` is the error kind that
    showed it, such as `overloaded`."""

    provider: str = Field(min_length=1, max_length=MAX_NAME)
    credential: str = Field(min_length=1, max_length=MAX_NAME)
    kind: str = Field(min_length=1, max_length=MAX_NAME)
    retry_at: datetime


class OutageSignalInterface(ABC):
    @abstractmethod
    async def report(self, outage: Outage, now: datetime) -> None:
        """Records that the provider is failing for the credential until
        `outage.retry_at`. A report whose retry time is earlier than the one
        held leaves the held one; one at or before `now` records nothing."""
        ...

    @abstractmethod
    async def current(self, provider: str, credential: str, now: datetime) -> Outage | None:
        """The outage known for the pair at `now`, or None: none was
        reported, its retry time has come, or the store cannot be reached."""
        ...

    @abstractmethod
    async def clear(self, provider: str, credential: str) -> None:
        """A call that succeeded ends what was known of the pair."""
        ...

    @abstractmethod
    def describe(self) -> str: ...

    @abstractmethod
    async def start(self) -> None:
        """Opened by the infra root at boot. An impl that holds no connection
        of its own returns None."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Closed by the infra root at shutdown, in reverse order of start."""
        ...
