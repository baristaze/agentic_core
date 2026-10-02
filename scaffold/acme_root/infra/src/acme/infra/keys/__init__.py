"""The key service: a capability that holds each tenant's wrapping key and
makes, unwraps, and re-wraps data keys under it. It never keeps a data key.
A data key leaves `generate` twice: in the clear, for the caller to use and
drop, and wrapped, for the caller to keep. Only the service unwraps it
again.

A data key is named by the tenant, the key it belongs to (a session's id),
and its version. A wrapped key is bound to that name: one presented under
another tenant, another key, or another version is refused, so a copy moved
to another session opens nothing. Destroying a data key is destroying every
wrapped copy of it, which is the keeper's to do: a key service holds no copy
to destroy.

A tenant's wrapping key has versions of its own. A key wrapped under an
older one still unwraps; `rewrap` moves it to the current one without the
data key ever leaving the service, so a rotation touches no data sealed
under it."""

from abc import ABC, abstractmethod
from uuid import UUID

from pydantic import Field

from acme.infra.base import InfraModel
from acme.infra.exceptions import KeyRefused

__all__ = ["DataKey", "KeyRefused", "KeyServiceInterface", "WrappedKey"]

DATA_KEY_BYTES = 32
"""A data key is 256 bits: an AES-256 key."""


class WrappedKey(InfraModel):
    """A data key as the service wrapped it: bytes only the service opens.
    `wrapping` names the wrapping key that wrapped it, as the service names
    it, and never holds a key."""

    blob: bytes = Field(min_length=1, repr=False)
    wrapping: str = Field(min_length=1)


class DataKey(InfraModel):
    """A fresh data key: `plaintext` to use and drop, `wrapped` to keep."""

    plaintext: bytes = Field(min_length=DATA_KEY_BYTES, max_length=DATA_KEY_BYTES, repr=False)
    wrapped: WrappedKey


class KeyServiceInterface(ABC):
    @abstractmethod
    async def generate(self, org_id: UUID, key_id: UUID, version: int) -> DataKey:
        """A new data key for version `version` of `key_id`, wrapped under
        the tenant's current wrapping key and bound to its name."""
        ...

    @abstractmethod
    async def unwrap(
        self, org_id: UUID, key_id: UUID, version: int, wrapped: WrappedKey
    ) -> bytes:
        """The data key `wrapped` holds. `KeyRefused` when it was wrapped
        under another name or another tenant's key, or was altered."""
        ...

    @abstractmethod
    async def rewrap(
        self, org_id: UUID, key_id: UUID, version: int, wrapped: WrappedKey
    ) -> WrappedKey:
        """The same data key, wrapped under the tenant's current wrapping
        key, and refused as `unwrap` refuses. The data key never leaves the
        service."""
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
