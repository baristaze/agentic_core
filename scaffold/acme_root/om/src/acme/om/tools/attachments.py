"""The text of a session's attachments, page by page: the narrow face the
engine's read tool reads. An attachment's bytes live in blob storage,
sealed like the step that names it, and turning a file into text (a
document's pages, a sheet's rows) is the adopter's. A root wires its reader
here; the null refuses."""

from abc import ABC, abstractmethod
from uuid import UUID

from pydantic import Field

from acme.om.base import Platform
from acme.om.context import TenantContext
from acme.om.steps.types.content import Attachment


class AttachmentText(Platform):
    """An attachment's text, page by page. A file with no pages of its own,
    such as plain text, is one page."""

    pages: tuple[str, ...] = Field(min_length=1)


class AttachmentReaderInterface(ABC):
    @abstractmethod
    async def read_text(
        self, ctx: TenantContext, session_id: UUID, attachment: Attachment
    ) -> AttachmentText | None:
        """The text of `attachment`, one the session's history holds, opened
        with the session's key and read from the tenant's own store, under
        the session. None when the file holds no text this reader reads,
        such as an image. `KeyRevoked` when the session's key is revoked:
        its content is erased."""
        ...
