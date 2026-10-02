"""Storage of the windows swimlane: the record of each artifact, written
once. A window itself is never stored; it is rebuilt from the steps. Every
operation takes org_id first.

A record is never rewritten, and the one delete is the purge, which runs
under the purge login when its session's history goes, or its tenant's
(ADR 1010): the serving logins may read and insert a record and nothing
more."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
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

    @abstractmethod
    async def read_artifacts(
        self, org_id: UUID, session_id: UUID | None, limit: int
    ) -> list[Artifact]:
        """At most `limit` of the session's artifacts, or of the tenant's when
        `session_id` is None, in id order: what a purge takes next."""
        ...

    @abstractmethod
    async def purge_artifacts(self, org_id: UUID, artifact_ids: Sequence[UUID]) -> int:
        """Under the purge login: the tenant's records among `artifact_ids`;
        returns how many went. An id another tenant holds is left as it is."""
        ...
