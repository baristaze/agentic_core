"""The keys of every session, below the managers. The sealing layer under
step storage asks for them by the tenant's id, as storage is asked, and the
privacy manager asks for them behind its permission checks.

A data key is in the clear only inside the call that uses it. What is kept
is its wrapped copy, in privacy storage, and only the key service unwraps
it."""

from abc import ABC, abstractmethod
from collections.abc import Set
from datetime import datetime
from uuid import UUID

from pydantic import Field

from acme.om.base import Platform


class OpenKey(Platform):
    """One version of a session's key, in the clear, for the call in hand."""

    version: int = Field(ge=1)
    key: bytes = Field(repr=False)


class SessionKeysInterface(ABC):
    @abstractmethod
    async def current(self, org_id: UUID, session_id: UUID) -> OpenKey:
        """The version new content is sealed under, made at version 1 when
        the session has none. `KeyRevoked` when the session's key is
        revoked."""
        ...

    @abstractmethod
    async def opened(self, org_id: UUID, session_id: UUID, versions: Set[int]) -> dict[int, bytes]:
        """The data key of each of `versions` the session still holds; a
        version destroyed, or never made, is left out."""
        ...

    @abstractmethod
    async def add_version(self, org_id: UUID, session_id: UUID) -> int:
        """A new version above every one before it, which new content is
        sealed under from now on; returns it. `KeyRevoked` when revoked."""
        ...

    @abstractmethod
    async def hash_key(self, org_id: UUID, session_id: UUID) -> bytes:
        """The key a keyed hash of the session's shape is made with: derived
        from the session's first version, so it goes when the key is
        revoked. `KeyRevoked` when it is."""
        ...

    @abstractmethod
    async def rewrap(self, org_id: UUID, before: datetime, limit: int) -> int:
        """The engine's half of a rotation of the tenant's wrapping key: at
        most `limit` live versions wrapped before `before`, each wrapped
        again under the current wrapping key, in place. No sealed content is
        read or written. Returns how many moved; a version destroyed
        meanwhile stays destroyed."""
        ...
