"""Storage of the windows swimlane: the record of each tool output, written
once. A window itself is never stored; it is rebuilt from the steps. Every
operation takes org_id first.

A record is never rewritten, and the one delete is the purge, which runs
under the purge login when its session's history goes, or its tenant's
(ADR 1010): the serving logins may read and insert a record and nothing
more."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from uuid import UUID

from acme.om.windows.types.tool_output import ToolOutput


class WindowStorageInterface(ABC):
    @abstractmethod
    async def write_tool_output(self, org_id: UUID, tool_output: ToolOutput) -> bool:
        """Writes a tool output's record, once. False, with nothing changed,
        when its id is written already: the same response kept again."""
        ...

    @abstractmethod
    async def read_tool_output(
        self, org_id: UUID, agent_session_id: UUID, tool_output_id: UUID
    ) -> ToolOutput | None:
        """The record of the session's tool output; None when the tenant's
        session holds no such tool_output."""
        ...

    @abstractmethod
    async def read_tool_outputs(
        self, org_id: UUID, agent_session_id: UUID | None, limit: int
    ) -> list[ToolOutput]:
        """At most `limit` of the session's tool outputs, or of the tenant's when
        `agent_session_id` is None, in id order: what a purge takes next."""
        ...

    @abstractmethod
    async def purge_tool_outputs(self, org_id: UUID, tool_output_ids: Sequence[UUID]) -> int:
        """Under the purge login: the tenant's records among `tool_output_ids`;
        returns how many went. An id another tenant holds is left as it is."""
        ...
