"""The keyed hash a request's header records of its rendered prompt. A hash in
a step's shape is keyed by its session, so once the session's key is
destroyed the hash confirms nothing about the content it was taken of. The
key service holds the key; this is the narrow face of it the windows read,
and a root wires the key service behind it."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.context import TenantContext


class PromptHashInterface(ABC):
    @abstractmethod
    async def keyed_hash(self, ctx: TenantContext, session_id: UUID, value: bytes) -> str:
        """A hash of `value` under the session's key: the same bytes give the
        same hash for as long as the key lives."""
        ...
