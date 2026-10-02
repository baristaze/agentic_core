"""The key service of a process that wired none. It is loud: a session's
content is never sealed under a key nobody holds, and never kept in the
clear for want of one, so every operation refuses and says why. The key
service has no quiet null; a test uses its memory impl."""

from uuid import UUID

from acme.infra.exceptions import InfraUnavailable
from acme.infra.keys import DataKey, KeyServiceInterface, WrappedKey

REASON = "no key service is wired, so no session key is made or opened"


class KeyServiceNullImpl(KeyServiceInterface):
    async def generate(self, org_id: UUID, key_id: UUID, version: int) -> DataKey:
        raise InfraUnavailable(REASON)

    async def unwrap(self, org_id: UUID, key_id: UUID, version: int, wrapped: WrappedKey) -> bytes:
        raise InfraUnavailable(REASON)

    async def rewrap(
        self, org_id: UUID, key_id: UUID, version: int, wrapped: WrappedKey
    ) -> WrappedKey:
        raise InfraUnavailable(REASON)

    def describe(self) -> str:
        return "keys=none"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None
