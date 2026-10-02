"""Storage of the windows swimlane: the record of each artifact, written
once. A window itself is never stored; it is rebuilt from the steps. Every
operation takes org_id first."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.windows.types.artifact import Artifact


class WindowStorageInterface(ABC):
    @abstractmethod
    async def write_artifact(self, org_id: UUID, artifact: Artifact) -> bool:
        """Writes an artifact's record, once. False, with nothing changed,
        when its id is written already: the same response kept again."""
        ...

    @abstractmethod
    async def read_artifact(
        self, org_id: UUID, session_id: UUID, artifact_id: UUID
    ) -> Artifact | None:
        """The record of the session's artifact; None when the tenant's
        session holds no such artifact."""
        ...
