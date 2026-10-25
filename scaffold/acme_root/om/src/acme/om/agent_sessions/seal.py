"""The seal a session's title takes before it reaches storage. A title is
content, like what a step says, so it is sealed under its session's key: a
database reader sees noise, revoking the key erases it, and the session
keeps its place. The key service holds the key; this is the narrow face of
it the sessions read, and a root wires the privacy namespace's seal behind
it. It takes the tenant's id, as storage does, so the sweep across tenants
seals a title as a call of the tenant's does."""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from uuid import UUID


class TitleSealInterface(ABC):
    @abstractmethod
    async def seal(self, org_id: UUID, agent_session_id: UUID, title: str) -> bytes:
        """`title` sealed under the current version of the session's key,
        bound to the tenant, the session, and the version, so a blob copied
        to another session opens nothing. `KeyRevoked` when the key is
        revoked."""
        ...

    @abstractmethod
    async def open(self, org_id: UUID, sealed: Mapping[UUID, bytes]) -> dict[UUID, str]:
        """The title of each session `sealed` names, by its id, from the blob
        `seal` made for it, all at once: the sessions' keys in one read, and
        opened together. A session whose version is destroyed is left out,
        its title with it. A blob no seal of this platform made is refused,
        never read as anything."""
        ...
