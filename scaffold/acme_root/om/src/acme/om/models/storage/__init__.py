"""Storage of the models swimlane: every version of every session's fill
set, written once. Every operation takes org_id first."""

from abc import ABC, abstractmethod
from uuid import UUID

from acme.om.models.types.fill import FillSet


class FillSetStorageInterface(ABC):
    @abstractmethod
    async def write_fill_set(self, org_id: UUID, fill_set: FillSet) -> bool:
        """Writes one version, once. False, with nothing changed, when its id
        is written already: a retry. A version the session holds already
        under another id is `PreconditionFailed`, with nothing written:
        another writer made that version first."""
        ...

    @abstractmethod
    async def read_fill_set(
        self, org_id: UUID, session_id: UUID, version: int | None
    ) -> FillSet | None:
        """One version of the session's fill set, or its latest when
        `version` is None; None when there is no such version."""
        ...
